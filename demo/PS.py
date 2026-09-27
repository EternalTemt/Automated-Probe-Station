"""
Заглушка зондовой станции (probe station, PS) — релейная матрица контактов.

На этом этапе это НЕ реальный драйвер МК, а заглушка с интерфейсом,
точно совпадающим с будущим драйвером — чтобы замена прошла без правок
контроллера (требование ТЗ):

- set_contacts(a: int, b: int) -> bool — скоммутировать пару контактов;
  возвращает True только после подтверждения коммутации (в заглушке всегда True);
- release() — разомкнуть все реле;
- сигналы: contactsChanged(a, b), released, error(str).

Живёт в worker-потоке вместе с K2636B и контроллером; вызывается контроллером
напрямую (один поток — одна очередь событий, это безопасно).

Правило безопасности (заложено в контроллере, здесь — напоминание):
напряжение (output=1) подаётся на образец только после того, как
set_contacts вернул True.
"""

from PySide6.QtCore import QObject, Signal


class PS(QObject):
    """Заглушка зондовой станции: коммутирует пару контактов (a, b)."""

    contactsChanged = Signal(int, int)  # подтверждено переключение на пару (a, b)
    released = Signal()                 # все реле разомкнуты
    error = Signal(str)                 # ошибка станции (в заглушке не возникает)

    # Диапазон контактов по умолчанию: MD2 — 30 контактов (по 15 на сторону),
    # это максимум из двух типов чипов; реальный драйвер узнаёт диапазон
    # из конфигурации станции.
    MAX_CONTACT = 15

    def __init__(self, parent=None, debug: bool = False):
        super().__init__(parent)
        self.debug = debug
        self.connected = False
        self._contacts: tuple[int, int] | None = None

    def connect(self) -> None:
        """«Подключиться» к станции. У заглушки нет физического порта —
        просто флаг готовности, чтобы контроллер мог отличить «не открыто»
        от «готово к коммутации»."""
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
        """Скоммутировать контакты a (сторона A) и b (сторона B).

        Возвращает True только после подтверждения коммутации. У заглушки
        подтверждение мгновенное и всегда успешное — так и задумано, это
        позволяет проверить всю цепочку «контроллер -> станция -> образец»
        без железа. Реальный драйвер будет ждать ACK от МК и выдерживать
        время переключения реле.
        """
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
        # Подтверждённая коммутация — оповещаем подписчиков (в заглушке GUI
        # на этот сигнал не подписан, но реальный драйвер будет его слать,
        # и контроллер уже умеет его слушать).
        self.contactsChanged.emit(a, b)
        return True

    def release(self) -> None:
        """Разомкнуть все реле станции (безопасное состояние)."""
        if self.debug:
            print("# PS: release (заглушка)")
        self._contacts = None
        self.released.emit()
