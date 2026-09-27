"""
Контроллер измерения: конечный автомат idle/started/paused/ended и
неблокирующий событийный цикл развёртки.

Живёт в worker-потоке (deviceThread) вместе с драйверами K2636B и PS и
вызывает их методы НАПРЯМУЮ — трое в одном потоке с одной очередью
событий, гонки невозможны (правило 3 раздела «Многопоточность» ТЗ).
GUI контроллер не вызывает напрямую — только через сигналы, соединённые
в main.py с Qt.ConnectionType.QueuedConnection.

Конечный автомат (ТЗ):
    idle -> started:      кнопка start (после проверки параметров, проверки
                          коммутации PS, установки compliance, output=1)
    started -> paused:    кнопка pause (цикл останавливается, output=0 —
                          напряжение снимается с образца, решение автора ТЗ)
    paused -> started:    кнопка resume (output=1, возврат напряжения
                          последней точки, выдержка Δt, продолжение цикла)
    started/paused -> ended -> idle: кнопка restart ИЛИ естественный конец
                          развёртки (output=0, V=0, сохранение JSON+CSV,
                          разблокировка полей, кнопка -> «start»)

Необлокирующий цикл (обязательная механика ТЗ): while-циклы и time.sleep
запрещены — заблокируют worker-поток, и pause/restart перестанут работать.
Одна точка развёртки = set_voltage -> выдержка Δt (QTimer.singleShot,
таймер живёт в worker-потоке) -> get_iv -> emit pointUpdated -> внутренний
сигнал sig_next_point (QueuedConnection) -> следующая точка. Так слоты
pause/stop обрабатываются между точками.

Порядок развёртки (ТЗ): исходная точка (0,0) -> максимум V2 -> минимум V1
-> исходная точка — чтобы было видно гистерезис.

Правило безопасности (заложено на будущее, ТЗ): напряжение (output=1)
подаётся только после того, как PS.set_contacts вернул True; иначе
старт отклоняется сигналом startFailed.

Сохранение данных — обязанность контроллера (только он обладает полнотой
данных): JSON (параметры + массивы точек + путь к PNG) и CSV (заголовок
параметрами + колонки U, I) в demo/data/{json,csv}/, имя sample_<X>_<время>.
"""

import csv
import json
import os
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot

from K2636B import K2636B
from PS import PS

# Состояния конечного автомата
IDLE = "idle"
STARTED = "started"
PAUSED = "paused"
ENDED = "ended"   # транзитное: после остановки/конца, до сброса в idle

# Диапазоны контактов по типам чипов (дублирует ps_widget.CHIP_RANGES —
# контроллер в worker-потоке не имеет права трогать GUI-виджеты, поэтому
# проверяет сам, из тех же данных, что и раньше: напряжение подаётся только
# на подтверждённую пару контактов).
CHIP_RANGES = {"MD2": (1, 15), "OP2": (1, 13)}

# Отсекаем заведомо бессмысленные развёртки: 100k точек по 10 мс — 17 минут,
# такое в интерфейс вводить не должны, но защита от деления/циклов нужна.
MAX_POINTS = 100_000

# Корень результатов — рядом с этим файлом (demo/), чтобы программа работала
# независимо от текущей директории запуска. Папки создаются при сохранении
# (os.makedirs(exist_ok=True)) — по требованию ТЗ.
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
JSON_DIR = os.path.join(DATA_DIR, "json")
CSV_DIR = os.path.join(DATA_DIR, "csv")


class Controller(QObject):
    """Состояние измерения, цикл развёртки, сохранение данных."""

    # --- Сигналы -> GUI (соединения с QueuedConnection — в main.py) ---
    startDone = Signal()            # измерение запущено (поля блокировать)
    startFailed = Signal(str)       # старт отклонён: причина (GUI остаётся в idle)
    pauseDone = Signal()            # цикл остановлен, output=0
    resumeDone = Signal()           # цикл продолжится после выдержки Δt
    stopDone = Signal(str)          # остановлено; str — путь к JSON ("" при ошибке)
    pointUpdated = Signal(float, float)  # (u, i) — точка развёртки -> график
    finished = Signal()             # естественный конец развёртки (-> idle)
    shutdownDone = Signal()         # приборы закрыты, поток можно гасить

    # --- Внутренний сигнал: самоперепланировка цикла развёртки ---
    # Соединён с next_point через QueuedConnection — так одна точка = одна
    # итерация очереди событий worker-потока, и pause/stop успевают вклиниться.
    sig_next_point = Signal()

    # --- Сигнал инициализации приборов ---
    # main.py эмитит его уже ПОСЛЕ старта deviceThread: open()/connect()
    # исполнятся в worker-потоке (требование ТЗ — ресурс pyvisa не должен
    # привязаться к GUI-потоку).
    startup = Signal()

    def __init__(self, k2636b: K2636B, ps: PS, parent=None):
        super().__init__(parent)
        self.k = k2636b
        self.ps = ps

        self.state = IDLE
        self.channel = "A"          # канал развёртки, по умолчанию A (по ТЗ)

        self._params: dict = {}
        self._voltages: list[float] = []  # план развёртки, В
        self._idx = 0                     # номер следующей точки плана
        self._u: list[float] = []         # измеренные напряжения
        self._i: list[float] = []         # измеренные токи
        # Поколение отложенных вызовов: pause/stop/завершение инкрементируют
        # счётчик, а запланированные через QTimer.singleShot замыкания
        # срабатывают только при совпадении поколения. Это отмена «встречных»
        # таймеров без хранения ссылок на них: проще и надёжнее, чем
        # отсоединение, т.к. QTimer.singleShot без родителя не отменяется.
        self._token = 0

        self.sig_next_point.connect(self.next_point, Qt.ConnectionType.QueuedConnection)

    # ================================================================== #
    # Инициализация и завершение (слоты worker-потока)                   #
    # ================================================================== #

    @Slot()
    def initialize(self) -> None:
        """Открыть приборы — вызывается сигналом инициализации ПОСЛЕ старта
        deviceThread (порядок задан в main.py)."""
        self.k.open()
        self.ps.connect()

    @Slot()
    def shutdown(self) -> None:
        """Завершение программы (порядок шагов — по разделу ТЗ «Завершение»).

        Выполняется в worker-потоке: здесь можно дёргать драйверы напрямую.
        """
        # 1. Если идёт измерение — остановить (переход в idle, как у кнопки
        #    restart, но без диалогов: данные сохраняем, пользователь их явно
        #    не отменял).
        if self.state in (STARTED, PAUSED):
            self._halt(save=True)
        # 2–3. Выключить напряжение (output обоих каналов в 0, уровни V в 0 —
        # защита образца и прибора) и закрыть приборы (реле — release()).
        # close()/release() сами ставят безопасные состояния, дублируем
        # явно, чтобы порядок «сначала снять напряжение, потом закрыть»
        # читался из кода, а не из реализации драйверов.
        try:
            self.k.set_output_A(False)
            self.k.set_output_B(False)
            self.k.set_voltage_A(0.0)
            self.k.set_voltage_B(0.0)
        except Exception as e:
            print(f"# controller: ошибка снятия напряжения при выходе: {e}")
        self.k.close()
        self.ps.release()
        # 4–5. main.py по shutdownDone делает deviceThread.quit()/wait() и
        # только потом завершает приложение.
        self.shutdownDone.emit()

    # ================================================================== #
    # Слоты команд GUI                                                    #
    # ================================================================== #

    @Slot(dict)
    def start(self, params: dict) -> None:
        """Команда «start»: проверка параметров -> коммутация PS -> compliance
        -> output=1 -> запуск цикла точек."""
        if self.state != IDLE:
            self.startFailed.emit("Измерение уже выполняется")
            return

        err = self._validate(params)
        if err is not None:
            self.startFailed.emit(err)
            return

        # Правило безопасности (ТЗ): напряжение — только после подтверждённой
        # коммутации. PS.set_contacts вернёт True лишь после подтверждения
        # (в заглушке — всегда, в реальном драйвере — после ACK МК).
        if not self.ps.set_contacts(params["contactA"], params["contactB"]):
            self.startFailed.emit(
                "Зондовая станция не подтвердила коммутацию контактов "
                f"A={params['contactA']}, B={params['contactB']}"
            )
            return

        # Compliance выставляем ДО включения output — иначе на образец подадим
        # неограниченный ток при первой точке (защита образца, ТЗ).
        self._set_compliance(params["compliance"])

        # Output включается только сейчас — на время измерения (решение
        # автора ТЗ). Драйвер сам перепроверит фактический статус и эмитнет
        # error при расхождении; контроллер проверяет ещё и по возврату —
        # двойная защита при работе с дорогим прибором.
        self._set_output(True)
        if not self._get_output():
            self._set_output(False)
            self.startFailed.emit("K2636B не подтвердил включение output")
            return

        # План развёртки строим после всех проверок — до первой точки.
        self._params = params
        self._voltages = self._build_sweep(params["V1"], params["V2"], params["Vs"])
        self._idx = 0
        self._u, self._i = [], []
        self.state = STARTED
        self.startDone.emit()
        # Цикл крутится через очередь событий: первая точка — следующей же
        # итерацией, не inline, чтобы очередь оставалась отзывчивой.
        self.sig_next_point.emit()

    @Slot()
    def pause(self) -> None:
        """Команда «pause»: остановить цикл, снять напряжение (output=0 —
        безопасность образца, решение автора ТЗ)."""
        if self.state != STARTED:
            return
        self._token += 1            # отменяет запланированные точки
        self.state = PAUSED
        self._set_output(False)
        self.pauseDone.emit()

    @Slot()
    def resume(self) -> None:
        """Команда «resume»: output=1, вернуть напряжение последней точки,
        выждать Δt (напряжение должно установиться на образце), продолжить."""
        if self.state != PAUSED:
            return
        self.state = STARTED
        self._set_output(True)
        # Возвращаем напряжение точки, на которой остановились: self._idx
        # указывает на СЛЕДУЮЩУЮ точку, последняя выставленная — _idx-1.
        last_v = self._voltages[self._idx - 1] if self._idx > 0 else 0.0
        self._set_voltage(last_v)
        self.resumeDone.emit()
        self._schedule(self._dt_ms(), self._measure_point)

    @Slot()
    def stop(self) -> None:
        """Команда «restart»: остановить, сохранить данные, сброс в idle."""
        if self.state not in (STARTED, PAUSED):
            return
        self._halt(save=True)

    @Slot(str)
    def set_channel(self, channel: str) -> None:
        """Выбор канала развёртки (сигнал actual_IV.channelChanged).
        Меняем только в idle: посреди развёртки канал уже зафиксирован."""
        if self.state == IDLE and channel in ("A", "B"):
            self.channel = channel

    # ================================================================== #
    # Слоты аварий драйверов                                              #
    # ================================================================== #

    @Slot()
    def on_connection_lost(self) -> None:
        """Прибор перестал отвечать (сигнал K2636B.connectionLost).

        Контроллер обязан остановить измерение и выключить output (ТЗ).
        Выключить output, скорее всего, уже не выйдет — прибор мёртв, —
        но попытка дешевле, чем пропуск: если отвалился именно опрос, а
        выход остался, это единственный способ снять напряжение.
        """
        print("# controller: потеряно соединение с K2636B — аварийная остановка")
        if self.state in (STARTED, PAUSED):
            self._token += 1
            try:
                self._set_output(False)
            except Exception:
                pass
            self.state = IDLE
            # startFailed показывает причину и оставляет GUI в idle —
            # reuse существующего канала уведомления вместо нового сигнала.
            self.startFailed.emit("Потеряно соединение с K2636B — измерение прервано")

    @Slot(str)
    def on_device_error(self, message: str) -> None:
        """Ошибка драйвера (несоответствие output и т.п.): измерение нельзя
        продолжать — те же действия, что при потере соединения."""
        print(f"# controller: ошибка прибора: {message}")
        if self.state in (STARTED, PAUSED):
            self._token += 1
            try:
                self._set_output(False)
            except Exception:
                pass
            self.state = IDLE
            self.startFailed.emit(f"Ошибка прибора: {message}")

    # ================================================================== #
    # Цикл развёртки (worker-поток, очередь событий)                      #
    # ================================================================== #

    @Slot()
    def next_point(self) -> None:
        """Одна итерация цикла: выставить напряжение точки и запланировать
        измерение через Δt. Слот внутреннего сигнала sig_next_point."""
        if self.state != STARTED:
            return
        if self._idx >= len(self._voltages):
            self._finish_sweep()    # естественный конец развёртки
            return
        v = self._voltages[self._idx]
        self._idx += 1
        self._set_voltage(v)
        # Выдержка Δт через таймер worker-потока (sleep запрещён ТЗ):
        # срабатывание таймера = измерение точки. Поколение (_token)
        # защищает от «встречных» срабатываний после pause/stop.
        self._schedule(self._dt_ms(), self._measure_point)

    def _measure_point(self) -> None:
        """Выдержка Δt истекла: снять (i, v), разослать точку, перепланировать."""
        if self.state != STARTED:
            return
        i, u = self._get_iv()
        self._u.append(u)
        self._i.append(i)
        self.pointUpdated.emit(u, i)
        # Перепланировка через внутренний сигнал: цикл крутится в очереди
        # событий, и pause/stop успевают обрабатываться между точками (ТЗ).
        self.sig_next_point.emit()

    # ================================================================== #
    # План развёртки                                                      #
    # ================================================================== #

    @staticmethod
    def _build_sweep(v1: float, v2: float, vs: float) -> list[float]:
        """План развёртки: (0,0) -> V2 -> V1 -> (0,0) с шагом Vs.

        Целочисленный счёт шагов (round), а не накопление float — иначе
        ошибки округления накапливаются и последняя точка уходит от 0.
        Точки поворота (V2 и V1) не дублируются.
        """
        n_up = round(v2 / vs)         # число шагов 0 -> V2
        n_down = round(v1 / vs)       # число шагов 0 -> V1 (отрицательно)
        plan = [round(i * vs, 6) for i in range(0, n_up + 1)]
        plan += [round(i * vs, 6) for i in range(n_up - 1, n_down - 1, -1)]
        plan += [round(i * vs, 6) for i in range(n_down + 1, 1)]
        return plan

    # ================================================================== #
    # Завершение и сохранение                                             #
    # ================================================================== #

    def _halt(self, save: bool) -> None:
        """Общая остановка из started/paused: output=0, V=0, сохранение,
        сброс в idle. Естественный конец развёртки идёт сюда же — таблица
        переходов ТЗ для restart и «конец развёртки» совпадает."""
        self._token += 1
        natural = self._idx >= len(self._voltages)
        self.state = ENDED
        self._set_output(False)
        self._set_voltage(0.0)

        path = ""
        if save:
            try:
                path = self._save()
            except Exception as e:
                # Сохранение не должно ронять worker-поток: сообщаем в консоль
                # и отдаём GUI пустой путь — поля разблокируются в любом случае.
                print(f"# controller: ошибка сохранения: {e}")

        self._params = {}
        self._voltages = []
        self._idx = 0
        self.state = IDLE
        # stopDone — всегда: по нему GUI разблокирует поля и сохранит PNG.
        self.stopDone.emit(path)
        if natural:
            # Естественный конец развёртки дополнительно помечаем finished
            # (по ТЗ — отдельный сигнал; GUI на него не обязан реагировать).
            self.finished.emit()

    def _finish_sweep(self) -> None:
        """Естественный конец развёртки — тот же путь, что у restart."""
        self._halt(save=True)

    def _save(self) -> str:
        """Сохранить JSON + CSV. Возвращает путь к JSON (для stopDone и
        для вывода пути к PNG в JSON — одно базовое имя у всех файлов)."""
        # Миллисекунды в штампе обязательны: два эксперимента, сохранённые
        # в одну секунду (например, ручной stop сразу после старта),
        # иначе получили бы одно имя и перезаписали бы друг друга.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        base = f"sample_{self._params['sample']}_{stamp}"

        os.makedirs(JSON_DIR, exist_ok=True)
        os.makedirs(CSV_DIR, exist_ok=True)

        json_path = os.path.join(JSON_DIR, base + ".json")
        csv_path = os.path.join(CSV_DIR, base + ".csv")
        # PNG пишет graph.py в GUI-потоке по stopDone; путь к нему кладём
        # в JSON заранее — по ТЗ JSON обязан содержать путь к графику.
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

        # CSV: заголовок параметрами комментариями (читается в Origin),
        # затем две колонки U, I.
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

    # ================================================================== #
    # Проверка параметров                                                 #
    # ================================================================== #

    @staticmethod
    def _validate(params: dict) -> str | None:
        """Вернуть текст ошибки или None. Спинбоксы GUI уже держат пределы,
        но контроллер — последний рубеж: сюда dict может прийти и не из GUI."""
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

    # ================================================================== #
    # Обёртки над драйвером (диспетчеризация по каналу)                   #
    # ================================================================== #

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
        """Запланировать вызов в очереди событий worker-потока через delay_ms.

        Замыкание помнит поколение (self._token на момент планирования);
        если с момента планирования был pause/stop/завершение — вызов
        молча отменяется. Отсюда нет опасности «встречной» точки после паузы.
        """
        self._token += 1
        token = self._token

        def wrapper():
            if token != self._token:
                return
            func()

        # QTimer.singleShot создаёт таймер в потоке, из которого вызван —
        # мы всегда в worker-потоке, значит срабатывание тоже там (ТЗ).
        QTimer.singleShot(delay_ms, wrapper)
