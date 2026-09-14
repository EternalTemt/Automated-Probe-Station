"""Драйверы приборов (QObject-адаптеры).

Живут в worker-потоке (см. ui/main_window.py). GUI общается с ними
только через сигналы/слоты; сами драйверы хранят кэш состояния и
выполняют блокирующий обмен с железом.

Пока реального протокола нет: если ресурс не открывается, объект
переходит в режим симуляции и возвращает псевдоизмерения — этого
хватает, чтобы отладить GUI и контроллер без приборов.
"""

import os
import time

from PySide6.QtCore import QObject, Signal, Slot


class K2636B(QObject):
    """Источник-измеритель Keithley 2636B (два SMU-канала A и B)."""

    newVoltage = Signal(float)          # подтверждение установки напряжения
    newPoint = Signal(float, float)     # измеренная точка (u, i)
    connectionLost = Signal()

    def __init__(self, resource: str = "/dev/usbtmc0", parent=None):
        super().__init__(parent)
        self.resource = resource
        self.simulated = True
        self._fd = None
        self._voltage = 0.0
        self._channel = "A"
        self.open()

    def open(self):
        """Открыть прибор. Сейчас — заглушка raw USBTMC."""
        try:
            self._fd = os.open(self.resource, os.O_RDWR)
            self.simulated = False
        except OSError:
            # прибора нет — работаем в симуляции
            self.simulated = True

    def close(self):
        if self._fd is not None:
            self._safe_off()
            os.close(self._fd)
            self._fd = None

    def _safe_off(self):
        """Выключить выходы перед закрытием (smua/smub output off)."""

    @Slot(str)
    def setChannel(self, channel: str):
        """Выбрать активный SMU-канал ('A' или 'B')."""
        self._channel = channel

    @Slot(float)
    def setVoltage(self, u: float):
        """Установить напряжение смещения на активном канале."""
        self._voltage = u
        if not self.simulated:
            pass  # os.write(self._fd, f"smu{...}.source.levelv={u}".encode())
        self.newVoltage.emit(u)

    @Slot(float, result=float)
    def measure(self, u: float) -> float:
        """Измерить ток при напряжении u, вернуть i и разослать точку."""
        if self.simulated:
            time.sleep(0.001)
            i = 0.05 * u * u  # псевдо-ВАХ для отладки
        else:
            i = 0.0  # os.write(... "print(smua.measure.iv())") + os.read + parse
        self.newPoint.emit(u, i)
        return i


class ProbeStation(QObject):
    """Релейная коммутация контактов чипа через микроконтроллер.

    30 контактов: по 15 на каждой из двух сторон (A и B). Для измерения
    выбирается пара контактов: a и b, каждый в диапазоне 1..15.
    МК получает команду по последовательному порту и замыкает реле.
    """

    N_CONTACTS = 15
    ACK_TIMEOUT = 1.0      # с, сколько ждём ответ МК на команду
    SETTLE_TIME = 0.1      # с, выдержка на физическое переключение реле

    contactsChanged = Signal(int, int)   # подтверждение: замкнуты контакты a, b
    released = Signal()                  # все реле разомкнуты
    error = Signal(str)                  # невалидный контакт / сбой связи с МК

    def __init__(self, resource: str = "/dev/ttyUSB0", parent=None):
        super().__init__(parent)
        self.resource = resource
        self.simulated = True
        self._a = 0   # 0 = контакт не выбран
        self._b = 0
        self._port = None
        self.open()

    def open(self):
        """Открыть последовательный порт МК; иначе — симуляция."""
        try:
            import serial
            self._port = serial.Serial(self.resource, baudrate=115200,
                                       timeout=self.ACK_TIMEOUT)
            self.simulated = False
        except Exception:
            # МК не подключён или pyserial не установлен — работаем в симуляции
            self.simulated = True

    def close(self):
        self.releaseAll()
        if self._port is not None:
            self._port.close()
            self._port = None

    def _transact(self, cmd: str) -> bool:
        """Отправить команду МК и дождаться подтверждения (ACK).

        Возвращает True, только если МК ответил "OK" до истечения
        ACK_TIMEOUT. Формат кадра и ответа согласовать с прошивкой.
        """
        if self.simulated:
            time.sleep(self.SETTLE_TIME)
            return True
        self._port.write((cmd + "\n").encode())
        reply = self._port.readline().decode(errors="ignore").strip()
        return reply.startswith("OK")

    def _valid(self, n: int) -> bool:
        return 1 <= n <= self.N_CONTACTS

    @Slot(int, int, result=bool)
    def setContacts(self, a: int, b: int) -> bool:
        """Замкнуть пару контактов: a — сторона A, b — сторона B.

        True возвращается только после подтверждения от МК и выдержки
        SETTLE_TIME — вызывающий код обязан проверить результат и не
        подавать напряжение, пока коммутация не подтверждена.
        """
        if not self._valid(a) or not self._valid(b):
            self.error.emit(f"контакты вне диапазона 1..{self.N_CONTACTS}: A={a}, B={b}")
            return False
        if not self._transact(f"RELAY A{a} B{b}"):
            self.error.emit(f"МК не подтвердил коммутацию A={a}, B={b} за {self.ACK_TIMEOUT} с")
            return False
        self._a, self._b = a, b
        self.contactsChanged.emit(a, b)
        return True

    @Slot(result=bool)
    def releaseAll(self) -> bool:
        """Разомкнуть все реле (с подтверждением от МК)."""
        if not self._transact("RELAY OFF"):
            self.error.emit("МК не подтвердил размыкание реле")
            return False
        self._a = self._b = 0
        self.released.emit()
        return True
