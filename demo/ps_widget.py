"""
Виджет выбора контактов зондовой станции (ps_widget).

По ТЗ:
- две ячейки (спинбокса): слева подпись «A:» и «B:»;
- допустимый диапазон: MD2 — 1..15, OP2 — 1..13 (MD2: 30 контактов, по 15
  на стороны A и B; OP2: 26 контактов, по 13 на стороны A и B);
- ячейки НЕДОСТУПНЫ, пока тип чипа не выбран; диапазон обновляется по сигналу
  chipTypeChanged от controller_widget (слот set_chip_type(str));
- выбранные контакты включаются в dict параметров при старте измерения
  (поля contactA, contactB) — забор значений делает controller_widget через
  get_contacts() (оба виджета живут в GUI-потоке, прямой вызов между ними
  безопасен — запрет ТЗ касается только межпоточных вызовов).
"""

from PySide6.QtWidgets import QFormLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

# Диапазоны контактов по типам чипов (утверждено автором ТЗ):
# MD2 — 30 контактов (15 на сторону), OP2 — 26 контактов (13 на сторону).
CHIP_RANGES = {
    "MD2": (1, 15),
    "OP2": (1, 13),
}


class PSWidget(QWidget):
    """Выбор пары контактов (A, B) для проверки ВАХ на образце чипа."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._chip_type: str | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Контакты зондовой станции</b>"))

        form = QFormLayout()
        # Ячейки созданы заранее, но недоступны: пока тип чипа не выбран,
        # значения спинбоксов не имеют смысла — защита от случайного старта
        # с контактами «по умолчанию».
        self.spin_a = QSpinBox()
        self.spin_b = QSpinBox()
        for spin in (self.spin_a, self.spin_b):
            spin.setRange(1, CHIP_RANGES["MD2"][1])  # временный максимум; точный — в set_chip_type
            spin.setEnabled(False)
        form.addRow(QLabel("A:"), self.spin_a)
        form.addRow(QLabel("B:"), self.spin_b)
        layout.addLayout(form)

        self.hint = QLabel("Выберите тип чипа выше")
        self.hint.setStyleSheet("color: gray;")
        layout.addWidget(self.hint)
        layout.addStretch(1)

    # ------------------------------------------------------------------ #
    # Слот: смена типа чипа (межвиджетная связь от controller_widget)    #
    # ------------------------------------------------------------------ #

    def set_chip_type(self, chip_type: str) -> None:
        """Обновить допустимый диапазон контактов по типу чипа.

        Тип чипа приходит от controller_widget (сигнал chipTypeChanged);
        всё, чего нет в CHIP_RANGES, считаем ошибкой вызова — оставляем
        ячейки недоступными и показываем причину.
        """
        if chip_type not in CHIP_RANGES:
            self.hint.setText(f"Неизвестный тип чипа: {chip_type}")
            return

        self._chip_type = chip_type
        lo, hi = CHIP_RANGES[chip_type]
        for spin in (self.spin_a, self.spin_b):
            spin.setRange(lo, hi)
            # setRange сам поджимает текущее значение в новые пределы — после
            # смены типа чипа старый номер контакта вне диапазона обрезается.
            spin.setEnabled(True)
        self.hint.setText(f"Диапазон {chip_type}: {lo}..{hi}")

    def set_enabled(self, enabled: bool) -> None:
        """Блокировка/разблокировка ячеек извне (контроллер блокирует все
        поля ввода на время измерения)."""
        available = enabled and self._chip_type is not None
        self.spin_a.setEnabled(available)
        self.spin_b.setEnabled(available)

    def get_contacts(self) -> tuple[int, int]:
        """Текущая выбранная пара контактов (a, b)."""
        return (self.spin_a.value(), self.spin_b.value())
