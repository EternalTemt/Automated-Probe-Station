from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QVBoxLayout, QWidget

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

        form = QFormLayout()
        self.combo_a = QComboBox()
        self.combo_b = QComboBox()
        for combo in (self.combo_a, self.combo_b):
            combo.setEnabled(False)
        form.addRow(QLabel("A:"), self.combo_a)
        form.addRow(QLabel("B:"), self.combo_b)
        layout.addLayout(form)

        self.hint = QLabel("Выберите тип чипа выше")
        self.hint.setStyleSheet("color: gray;")
        layout.addWidget(self.hint)
        layout.addStretch(1)

    def set_chip_type(self, chip_type: str) -> None:
        """Обновить списки доступных контактов по типу чипа."""
        if chip_type not in CHIP_RANGES:
            self.hint.setText(f"Неизвестный тип чипа: {chip_type}")
            return

        self._chip_type = chip_type
        lo, hi = CHIP_RANGES[chip_type]
        for combo in (self.combo_a, self.combo_b):
            combo.clear()
            combo.addItems([str(n) for n in range(lo, hi + 1)])
            combo.setEnabled(True)
        self.hint.setText(f"Диапазон {chip_type}: {lo}..{hi}")

    def set_enabled(self, enabled: bool) -> None:
        """Блокировка/разблокировка ячеек извне."""
        available = enabled and self._chip_type is not None
        self.combo_a.setEnabled(available)
        self.combo_b.setEnabled(available)

    def get_contacts(self) -> tuple[int, int]:
        """Текущая выбранная пара контактов (a, b)."""
        return (int(self.combo_a.currentText()), int(self.combo_b.currentText()))
