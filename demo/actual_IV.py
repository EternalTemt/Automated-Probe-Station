"""
Виджет живых значений V/I выбранного канала прибора (actual_IV).

По ТЗ:
- показывает актуальные значения напряжения U (в вольтах) и тока I (в амперах,
  запись через экспоненту — ожидаются токи порядка пикоампер);
- радиокнопки выбора канала A / B (по умолчанию A); при смене эмитит
  сигнал channelChanged(str) — его слушает контроллер (развёртка идёт по
  выбранному каналу);
- данные получает НАПРЯМЮЮ подпиской на сигнал K2636B.newIV_A / newIV_B
  (разрешённое ТЗ исключение: подписка на сигнал — это не вызов метода).
  Так живые значения не зависят от логики измерения: их видно и между
  развёртками, если контроллер что-то меряет.

Виджет живёт в GUI-потоке; соединения с сигналами драйвера указываются
в main.py с Qt.ConnectionType.QueuedConnection.
"""

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

    # Межвиджетная связь -> контроллер (развёртка идёт по выбранному каналу)
    channelChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._channel = "A"   # канал по умолчанию — A (по ТЗ)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Текущие значения</b>"))

        # Две строки: тип величины + единицы + само значение.
        # Значение — отдельный QLabel справа, чтобы было видно из кода,
        # что обновляется; ширина фиксированная, чтобы строки не «прыгали».
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

        # Радиокнопки канала: виджет показывает значения выбранного канала,
        # а контроллер ведёт развёртку по нему же.
        radios = QHBoxLayout()
        radios.addWidget(QLabel("Канал:"))
        self.radio_a = QRadioButton("A")
        self.radio_b = QRadioButton("B")
        self.radio_a.setChecked(True)   # по умолчанию канал A
        self._group = QButtonGroup(self)
        self._group.addButton(self.radio_a)
        self._group.addButton(self.radio_b)
        self._group.buttonClicked.connect(self._on_channel_clicked)
        radios.addWidget(self.radio_a)
        radios.addWidget(self.radio_b)
        radios.addStretch(1)
        layout.addLayout(radios)
        layout.addStretch(1)

    # ------------------------------------------------------------------ #
    # Слоты-подписки на сигналы драйвера K2636B                          #
    # ------------------------------------------------------------------ #

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
        # Напряжение — с знаком и разумной точностью (задаём десятки мВ).
        self.u_value.setText(f"{v:+.3f}")
        # Ток — через экспоненту: рабочие токи уходят в пикоамперный диапазон,
        # десятичная запись дала бы нечитаемые нули.
        self.i_value.setText(f"{i:.3e}")

    # ------------------------------------------------------------------ #
    # Выбор канала                                                       #
    # ------------------------------------------------------------------ #

    def _on_channel_clicked(self, button) -> None:
        self._channel = button.text()
        # Сбрасываем показания: значения другого канала относились к нему,
        # показывать их на выбранном канале было бы вводом в заблуждение.
        self.u_value.setText("—")
        self.i_value.setText("—")
        self.channelChanged.emit(self._channel)

    def current_channel(self) -> str:
        return self._channel
