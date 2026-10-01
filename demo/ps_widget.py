from PySide6.QtWidgets import QFormLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

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
        self.spin_a = QSpinBox()
        self.spin_b = QSpinBox()
        for spin in (self.spin_a, self.spin_b):
            spin.setRange(1, CHIP_RANGES["MD2"][1])
            spin.setEnabled(False)
        form.addRow(QLabel("A:"), self.spin_a)
        form.addRow(QLabel("B:"), self.spin_b)
        layout.addLayout(form)

        self.hint = QLabel("Выберите тип чипа выше")
        self.hint.setStyleSheet("color: gray;")
        layout.addWidget(self.hint)
        layout.addStretch(1)

    def set_chip_type(self, chip_type: str) -> None:
        """Обновить допустимый диапазон контактов по типу чипа."""

        if chip_type not in CHIP_RANGES:
            self.hint.setText(f"Неизвестный тип чипа: {chip_type}")
            return

        self._chip_type = chip_type
        lo, hi = CHIP_RANGES[chip_type]
        for spin in (self.spin_a, self.spin_b):
            spin.setRange(lo, hi)
            spin.setEnabled(True)
        self.hint.setText(f"Диапазон {chip_type}: {lo}..{hi}")

    def set_enabled(self, enabled: bool) -> None:
        """Блокировка/разблокировка ячеек извне."""
        available = enabled and self._chip_type is not None
        self.spin_a.setEnabled(available)
        self.spin_b.setEnabled(available)

    def get_contacts(self) -> tuple[int, int]:
        """Текущая выбранная пара контактов (a, b)."""
        return (self.spin_a.value(), self.spin_b.value())
