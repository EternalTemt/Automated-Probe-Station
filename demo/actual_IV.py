from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


class ActualIV(QWidget):
    """Живые значения U и I канала A или B + выбор канала."""

    channelChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._channel = "A"

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Текущие значения</b>"))

        grid = QGridLayout()
        self.u_value = QLabel("—")
        self.i_value = QLabel("—")
        for col, w in ((2, 110),):
            self.u_value.setMinimumWidth(w)
            self.i_value.setMinimumWidth(w)
        self.u_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.i_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        grid.addWidget(QLabel("Напряжение U"), 0, 0)
        grid.addWidget(QLabel("В"), 0, 1)
        grid.addWidget(self.u_value, 0, 2)
        grid.addWidget(QLabel("Ток I"), 1, 0)
        grid.addWidget(QLabel("А"), 1, 1)
        grid.addWidget(self.i_value, 1, 2)
        layout.addLayout(grid)

        radios = QHBoxLayout()
        radios.addWidget(QLabel("Канал:"))
        self.radio_a = QRadioButton("A")
        self.radio_b = QRadioButton("B")
        self.radio_a.setChecked(True)
        self._group = QButtonGroup(self)
        self._group.addButton(self.radio_a)
        self._group.addButton(self.radio_b)
        self._group.buttonClicked.connect(self._on_channel_clicked)
        radios.addWidget(self.radio_a)
        radios.addWidget(self.radio_b)
        radios.addStretch(1)
        layout.addLayout(radios)
        layout.addStretch(1)

    def on_iv_A(self, iv: tuple) -> None:
        """Свежая пара (i, v) канала A — показываем, только если канал A выбран."""
        if self._channel == "A":
            self._show(iv)

    def on_iv_B(self, iv: tuple) -> None:
        """Свежая пара (i, v) канала B — показываем, только если канал B выбран."""
        if self._channel == "B":
            self._show(iv)

    def _show(self, iv: tuple) -> None:
        i, v = iv[0], iv[1]
        self.u_value.setText(f"{v:+.3f}")
        self.i_value.setText(f"{i:.3e}")

    def _on_channel_clicked(self, button) -> None:
        self._channel = button.text()
        self.u_value.setText("—")
        self.i_value.setText("—")
        self.channelChanged.emit(self._channel)

    def current_channel(self) -> str:
        return self._channel
