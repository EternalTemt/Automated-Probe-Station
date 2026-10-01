import csv
import json
import os
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot

from K2636B import K2636B
from PS import PS

IDLE = "idle"
MEASUREMENTS = "measurements"
WAITING = "waiting"

CHIP_RANGES = {"MD2": (1, 15), "OP2": (1, 13)}

MAX_POINTS = 100_000

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
JSON_DIR = os.path.join(DATA_DIR, "json")
CSV_DIR = os.path.join(DATA_DIR, "csv")


class Controller(QObject):
    """Состояние измерения, цикл развёртки, сохранение данных."""

    startDone = Signal()
    startFailed = Signal(str)
    pauseDone = Signal()
    resumeDone = Signal()
    stopDone = Signal(str)
    pointUpdated = Signal(float, float)
    finished = Signal()
    shutdownDone = Signal()

    sig_next_point = Signal()

    startup = Signal()

    def __init__(self, k2636b: K2636B, ps: PS, parent=None):
        super().__init__(parent)
        self.k = k2636b
        self.ps = ps

        self.state = IDLE
        self.channel = "A"

        self._params: dict = {}
        self._voltages: list[float] = []
        self._idx = 0
        self._u: list[float] = []
        self._i: list[float] = []
        self._token = 0

        self.sig_next_point.connect(self.next_point, Qt.ConnectionType.QueuedConnection)

    @Slot()
    def initialize(self) -> None:
        """Открыть приборы (worker-поток)."""
        self.k.open()
        self.ps.connect()

    @Slot()
    def shutdown(self) -> None:
        """Завершение программы: снять напряжение, закрыть приборы."""
        if self.state in (MEASUREMENTS, WAITING):
            self._halt(save=True)
        try:
            self.k.set_output_A(False)
            self.k.set_output_B(False)
            self.k.set_voltage_A(0.0)
            self.k.set_voltage_B(0.0)
        except Exception as e:
            print(f"# controller: ошибка снятия напряжения при выходе: {e}")
        self.k.close()
        self.ps.release()
        self.shutdownDone.emit()

    @Slot(dict)
    def start(self, params: dict) -> None:
        """Команда «start»: проверка, коммутация, output=1, запуск цикла."""
        if self.state != IDLE:
            self.startFailed.emit("Измерение уже выполняется")
            return

        err = self._validate(params)
        if err is not None:
            self.startFailed.emit(err)
            return

        if not self.ps.set_contacts(params["contactA"], params["contactB"]):
            self.startFailed.emit(
                "Зондовая станция не подтвердила коммутацию контактов "
                f"A={params['contactA']}, B={params['contactB']}"
            )
            return

        self._set_compliance(params["compliance"])

        self._set_output(True)
        if not self._get_output():
            self._set_output(False)
            self.startFailed.emit("K2636B не подтвердил включение output")
            return

        self._params = params
        self._voltages = self._build_sweep(params["V1"], params["V2"], params["Vs"])
        self._idx = 0
        self._u, self._i = [], []
        self.state = MEASUREMENTS
        self.startDone.emit()
        self.sig_next_point.emit()

    @Slot()
    def pause(self) -> None:
        """Команда «pause»: остановить цикл, снять напряжение (output=0)."""
        if self.state != MEASUREMENTS:
            return
        self._token += 1
        self.state = WAITING
        self._set_output(False)
        self.pauseDone.emit()

    @Slot()
    def resume(self) -> None:
        """Команда «resume»: вернуть напряжение, выждать delay, продолжить."""
        if self.state != WAITING:
            return
        self.state = MEASUREMENTS
        self._set_output(True)
        last_v = self._voltages[self._idx - 1] if self._idx > 0 else 0.0
        self._set_voltage(last_v)
        self.resumeDone.emit()
        self._schedule(self._dt_ms(), self._measure_point)

    @Slot()
    def stop(self) -> None:
        """Команда «stop»: остановить, сохранить данные, сброс в idle."""
        if self.state not in (MEASUREMENTS, WAITING):
            return
        self._halt(save=True)

    @Slot(str)
    def set_channel(self, channel: str) -> None:
        """Выбор канала развёртки (только в idle)."""
        if self.state == IDLE and channel in ("A", "B"):
            self.channel = channel

    @Slot()
    def on_connection_lost(self) -> None:
        """Обработка потери соединения с K2636B: остановка, output=0."""
        print("# controller: потеряно соединение с K2636B — аварийная остановка")
        if self.state in (MEASUREMENTS, WAITING):
            self._token += 1
            try:
                self._set_output(False)
            except Exception:
                pass
            self.state = IDLE
            self.startFailed.emit("Потеряно соединение с K2636B — измерение прервано")

    @Slot(str)
    def on_device_error(self, message: str) -> None:
        """Обработка ошибки драйвера: та же остановка, что при потере соединения."""
        print(f"# controller: ошибка прибора: {message}")
        if self.state in (MEASUREMENTS, WAITING):
            self._token += 1
            try:
                self._set_output(False)
            except Exception:
                pass
            self.state = IDLE
            self.startFailed.emit(f"Ошибка прибора: {message}")

    @Slot()
    def next_point(self) -> None:
        """Одна итерация цикла: выставить напряжение точки, запланировать измерение."""
        if self.state != MEASUREMENTS:
            return
        if self._idx >= len(self._voltages):
            self._finish_sweep()
            return
        v = self._voltages[self._idx]
        self._idx += 1
        self._set_voltage(v)
        self._schedule(self._dt_ms(), self._measure_point)

    def _measure_point(self) -> None:
        """Задержка истекла: снять (i, v), разослать точку, перепланировать."""
        if self.state != MEASUREMENTS:
            return
        i, u = self._get_iv()
        self._u.append(u)
        self._i.append(i)
        self.pointUpdated.emit(u, i)
        self.sig_next_point.emit()

    @staticmethod
    def _build_sweep(v1: float, v2: float, vs: float) -> list[float]:
        """План развёртки: (0,0) -> V2 -> V1 -> (0,0) с шагом Vs."""
        n_up = round(v2 / vs)
        n_down = round(v1 / vs)
        plan = [round(i * vs, 6) for i in range(0, n_up + 1)]
        plan += [round(i * vs, 6) for i in range(n_up - 1, n_down - 1, -1)]
        plan += [round(i * vs, 6) for i in range(n_down + 1, 1)]
        return plan

    def _halt(self, save: bool, natural: bool = False) -> None:
        """Переход measurements/waiting -> idle: output=0, V=0, сохранение, сброс."""
        self._token += 1
        self._set_output(False)
        self._set_voltage(0.0)

        path = ""
        if save:
            try:
                path = self._save()
            except Exception as e:
                print(f"# controller: ошибка сохранения: {e}")

        self._params = {}
        self._voltages = []
        self._idx = 0
        self.state = IDLE
        self.stopDone.emit(path)
        if natural:
            self.finished.emit()

    def _finish_sweep(self) -> None:
        """Естественный конец развёртки — переход в idle с эмиссией finished."""
        self._halt(save=True, natural=True)

    def _save(self) -> str:
        """Сохранить JSON + CSV, вернуть путь к JSON."""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        base = f"sample_{self._params['sample']}_{stamp}"

        os.makedirs(JSON_DIR, exist_ok=True)
        os.makedirs(CSV_DIR, exist_ok=True)

        json_path = os.path.join(JSON_DIR, base + ".json")
        csv_path = os.path.join(CSV_DIR, base + ".csv")
        png_path = os.path.join(DATA_DIR, "graphs", base + ".png")

        data = {
            "params": {
                "sample": self._params["sample"],
                "chipType": self._params["chipType"],
                "V1": self._params["V1"],
                "V2": self._params["V2"],
                "Vs": self._params["Vs"],
                "dt": self._params["dt"],
                "compliance": self._params["compliance"],
                "channel": self.channel,
                "contactA": self._params["contactA"],
                "contactB": self._params["contactB"],
            },
            "u": list(self._u),
            "i": list(self._i),
            "graph_png": png_path,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            f.write(f"# sample={self._params['sample']}\n")
            for key in ("chipType", "V1", "V2", "Vs", "dt", "compliance",
                        "contactA", "contactB"):
                f.write(f"# {key}={self._params[key]}\n")
            f.write(f"# channel={self.channel}\n")
            writer = csv.writer(f)
            writer.writerow(["U", "I"])
            writer.writerows(zip(self._u, self._i))

        return json_path

    @staticmethod
    def _validate(params: dict) -> str | None:
        """Вернуть текст ошибки проверки параметров или None."""
        try:
            v1, v2, vs = params["V1"], params["V2"], params["Vs"]
            dt, comp = params["dt"], params["compliance"]
            chip = params["chipType"]
            a, b = params["contactA"], params["contactB"]
            sample = params["sample"]
        except KeyError as e:
            return f"Неполные параметры измерения: нет поля {e}"

        if not sample:
            return "Номер образца пуст"
        if not (-1.0 <= v1 < 0.0):
            return f"V1={v1} вне допустимого диапазона [-1.000; 0) В"
        if not (0.0 < v2 <= 1.0):
            return f"V2={v2} вне допустимого диапазона (0; +1.000] В"
        if not (0.0 < vs <= 2.0):
            return f"Vs={vs} должен быть положительным"
        if dt < 0.0:
            return f"Δt={dt} не может быть отрицательным"
        if not (1e-9 <= comp <= 0.1):
            return f"compliance={comp} вне диапазона 1 нА…100 мА"
        if chip not in CHIP_RANGES:
            return f"Неизвестный тип чипа: {chip}"
        lo, hi = CHIP_RANGES[chip]
        if not (lo <= a <= hi and lo <= b <= hi):
            return f"Контакты вне диапазона {chip} ({lo}..{hi}): A={a}, B={b}"
        if len(Controller._build_sweep(v1, v2, vs)) > MAX_POINTS:
            return "Слишком много точек развёртки — увеличьте Vs"
        return None

    def _set_output(self, on: bool) -> None:
        if self.channel == "A":
            self.k.set_output_A(on)
        else:
            self.k.set_output_B(on)

    def _get_output(self) -> bool:
        return self.k.get_output_A() if self.channel == "A" else self.k.get_output_B()

    def _set_voltage(self, v: float) -> None:
        if self.channel == "A":
            self.k.set_voltage_A(v)
        else:
            self.k.set_voltage_B(v)

    def _get_iv(self) -> tuple[float, float]:
        return self.k.get_iv_A() if self.channel == "A" else self.k.get_iv_B()

    def _set_compliance(self, limit: float) -> None:
        if self.channel == "A":
            self.k.set_compliance_A(limit)
        else:
            self.k.set_compliance_B(limit)

    def _dt_ms(self) -> int:
        return int(round(self._params.get("dt", 0.0) * 1000))

    def _schedule(self, delay_ms: int, func) -> None:
        """Запланировать вызов в очереди событий через delay_ms (проверка поколения)."""
        self._token += 1
        token = self._token

        def wrapper():
            if token != self._token:
                return
            func()

        QTimer.singleShot(delay_ms, wrapper)
