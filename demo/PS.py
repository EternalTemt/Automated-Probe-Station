from PySide6.QtCore import QObject, Signal


class PS(QObject):
    """Заглушка зондовой станции: коммутирует пару контактов (a, b)."""

    contactsChanged = Signal(int, int)
    released = Signal()
    error = Signal(str)

    MAX_CONTACT = 15

    def __init__(self, parent=None, debug: bool = False):
        super().__init__(parent)
        self.debug = debug
        self.connected = False
        self._contacts: tuple[int, int] | None = None

    def connect(self) -> None:
        """«Подключиться» к станции (флаг готовности)."""
        if self.debug:
            print("# PS: connect (заглушка)")
        self.connected = True

    def close(self) -> None:
        """Закрыть «соединение» со станцией (симметрично connect)."""
        if self.debug:
            print("# PS: close (заглушка)")
        self.connected = False
        self._contacts = None

    def set_contacts(self, a: int, b: int) -> bool:
        """Скоммутировать контакты a (сторона A) и b (сторона B)."""
        if self.debug:
            print(f"# PS: set_contacts({a}, {b}) (заглушка)")
        if not self.connected:
            self.error.emit("PS: станция не подключена")
            return False
        if not (1 <= a <= self.MAX_CONTACT and 1 <= b <= self.MAX_CONTACT):
            self.error.emit(
                f"PS: контакты вне диапазона 1..{self.MAX_CONTACT}: a={a}, b={b}"
            )
            return False

        self._contacts = (a, b)
        self.contactsChanged.emit(a, b)
        return True

    def release(self) -> None:
        """Разомкнуть все реле станции (безопасное состояние)."""
        if self.debug:
            print("# PS: release (заглушка)")
        self._contacts = None
        self.released.emit()
