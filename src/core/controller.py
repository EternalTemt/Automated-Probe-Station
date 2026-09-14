"""Контроллер эксперимента — конечный автомат и цикл развёртки.

Живёт в том же worker-потоке, что и драйверы приборов, поэтому вызывает
их методы напрямую. GUI управляет им только через сигналы/слоты.

Цикл измерений не блокирует поток: next_point() делает одну точку и
перепланирует себя через sig_next_point с Qt.QueuedConnection —
получается событийный цикл, в котором обрабатываются и слоты GUI
(pause/stop), и ответы приборов.

Данные эксперимента — единый dict self.e (как в reference/dsr),
читаемый GUI под QReadLocker.
"""

import json
import time
from enum import IntEnum

from PySide6.QtCore import QObject, QDateTime, QReadWriteLock, QReadLocker, \
    QWriteLocker, Signal, Slot, Qt


class Status(IntEnum):
    idle = 0
    started = 1
    paused = 2
    ended = 3


class SweepController(QObject):
    """Развёртка V1 -> V2 с шагом Vs и задержкой dt на точку."""

    # GUI -> контроллер
    startDone = Signal()        # эксперимент запущен
    startFailed = Signal(str)   # запуск отклонён (напр., МК не подтвердил коммутацию)
    pauseDone = Signal()
    resumeDone = Signal()
    stopDone = Signal(str)      # путь к сохранённому файлу ("" — не сохранялось)

    # контроллер -> GUI (данные)
    dataChanged = Signal()      # массивы x/y дополнились — перечитать e под локом
    pointUpdated = Signal(float, float)  # последняя точка (u, i) для живых полей

    # внутренний сигнал-«самопланировщик» цикла
    sig_next_point = Signal()

    def __init__(self, k2636b, probe_station=None, parent=None):
        super().__init__(parent)
        self.k2636b = k2636b
        self.probe = probe_station
        self.e = {"status": Status.idle, "x": [], "y": []}
        self.eLock = QReadWriteLock()
        self.sig_next_point.connect(self.next_point, Qt.QueuedConnection)

    # ---------- слоты от GUI ----------

    @Slot(dict)
    def start(self, e: dict):
        """Начать эксперимент. e = {sample, V1, V2, Vs, dt, contactA, contactB}."""
        with QWriteLocker(self.eLock):
            self.e = e
            self.e["x"] = []
            self.e["y"] = []
            self.e["status"] = Status.started
            self.e["dateTime"] = QDateTime.currentDateTime().toString(Qt.ISODate)
            self.e["_u"] = self.e["V1"]
        if self.probe is not None:
            # безопасность: напряжение подаём только после подтверждённой
            # коммутации реле — МК обязан ответить ACK (см. ProbeStation)
            if not self.probe.setContacts(self.e["contactA"], self.e["contactB"]):
                with QWriteLocker(self.eLock):
                    self.e["status"] = Status.idle
                self.startFailed.emit("коммутация контактов не подтверждена")
                return
        self.startDone.emit()
        self.sig_next_point.emit()

    @Slot()
    def pause(self):
        with QWriteLocker(self.eLock):
            if self.e["status"] == Status.started:
                self.e["status"] = Status.paused
        self.pauseDone.emit()

    @Slot()
    def resume(self):
        with QWriteLocker(self.eLock):
            if self.e["status"] == Status.paused:
                self.e["status"] = Status.started
        self.resumeDone.emit()
        self.sig_next_point.emit()

    @Slot()
    def stop(self):
        """Остановить. stopDone придёт, когда текущая точка завершится."""
        with QWriteLocker(self.eLock):
            self.e["status"] = Status.ended
        if self.e.get("status") == Status.ended and not self.e.get("_running"):
            self._finish()

    # ---------- цикл ----------

    @Slot()
    def next_point(self):
        with QReadLocker(self.eLock):
            status = self.e["status"]
            u = self.e.get("_u")
        if status == Status.ended:
            self._finish()
            return
        if status != Status.started:
            return  # paused — ждём resume

        self.k2636b.setVoltage(u)
        time.sleep(self.e["dt"])   # пауза в worker-потоке, GUI не морозится
        i = self.k2636b.measure(u)

        with QWriteLocker(self.eLock):
            self.e["x"].append(u)
            self.e["y"].append(i)
            self.e["_u"] = u + self.e["Vs"]
            done = (self.e["_u"] - self.e["V2"]) * self.e["Vs"] > 0
            if done:
                self.e["status"] = Status.ended

        self.pointUpdated.emit(u, i)
        self.dataChanged.emit()

        if done:
            self._finish()
        else:
            self.sig_next_point.emit()

    def _finish(self):
        """Завершение: сохранить данные в JSON и разослать stopDone."""
        path = ""
        with QReadLocker(self.eLock):
            if self.e["x"]:
                name = self.e.get("sample") or "run"
                stamp = QDateTime.currentDateTime().toString("yyyyMMdd_hhmmss")
                path = f"data/{stamp}_{name}.json"
        if path:
            import os
            os.makedirs("data", exist_ok=True)
            with QReadLocker(self.eLock):
                snapshot = {k: v for k, v in self.e.items() if not k.startswith("_")}
            snapshot["status"] = int(snapshot["status"])
            with open(path, "w") as f:
                json.dump(snapshot, f, indent="\t")
        self.stopDone.emit(path)


class ContactCheckController(QObject):
    """Проверка всех 30 контактов релейной матрицы одной кнопкой.

    Последовательно замыкает пары (1,1)...(15,15) — так охватываются
    все 15 контактов стороны A и все 15 стороны B. Каждый шаг идёт через
    подтверждённую ProbeStation.setContacts (ACK от МК + SETTLE_TIME),
    поэтому следующая команда коммутации отправляется только после того,
    как предыдущая физически выполнена — конфликтов переключения нет.

    Цикл событийный (sig_next + QueuedConnection), как у SweepController:
    слот stop() успевает обработаться между шагами.
    """

    testStarted = Signal()
    testProgress = Signal(int, int, bool)  # текущая пара (a, b), успех коммутации
    testDone = Signal(bool, int)           # прерван?, сколько пар проверено
    testRefused = Signal(str)              # запуск отклонён (занят экспериментом и т.п.)

    sig_next = Signal()

    def __init__(self, probe, sweep=None, parent=None):
        super().__init__(parent)
        self.probe = probe
        self.sweep = sweep        # SweepController — чтобы не мешать эксперименту
        self._running = False
        self._aborted = False
        self._i = 0
        self._n_checked = 0
        self.sig_next.connect(self.next_step, Qt.QueuedConnection)

    @Slot()
    def start(self):
        if self._running:
            return
        if self.sweep is not None:
            with QReadLocker(self.sweep.eLock):
                busy = self.sweep.e["status"] in (Status.started, Status.paused)
            if busy:
                self.testRefused.emit("идёт эксперимент — тест контактов недоступен")
                return
        self._running = True
        self._aborted = False
        self._i = 1
        self._n_checked = 0
        self.testStarted.emit()
        self.sig_next.emit()

    @Slot()
    def stop(self):
        """Остановить тест после завершения текущего (подтверждённого) шага."""
        self._aborted = True

    @Slot()
    def next_step(self):
        if not self._running:
            return
        if self._aborted or self._i > self.probe.N_CONTACTS:
            self._running = False
            self.probe.releaseAll()
            self.testDone.emit(self._aborted, self._n_checked)
            return
        ok = self.probe.setContacts(self._i, self._i)
        self.testProgress.emit(self._i, self._i, ok)
        if not ok:
            # сбой связи с МК — дальше переключаться опасно, прекращаем
            self._running = False
            self.testDone.emit(True, self._n_checked)
            return
        self._n_checked += 1
        self._i += 1
        self.sig_next.emit()
