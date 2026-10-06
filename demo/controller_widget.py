from PySide6.QtCore import QRegularExpression, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

V1_MIN, V1_MAX = -1.000, -0.001
V2_MIN, V2_MAX = 0.001, 1.000
I_MAX_MIN, I_MAX_MAX = 1e-9, 0.1


class ControllerWidget(QWidget):
    """Поля параметров измерения + кнопки start/pause/resume и stop."""

    sig_start = Signal(dict)
    sig_pause = Signal()
    sig_resume = Signal()
    sig_stop = Signal()

    chipTypeChanged = Signal(str)
    scaleChanged = Signal(bool)

    def __init__(self, ps_widget=None, session=None, parent=None):
        """Создать поля ввода и кнопки; ps_widget даёт контакты, session — папку запуска и лимиты."""
        super().__init__(parent)
        self._ps_widget = ps_widget
        self._session = session
        self._state = "idle"

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.sample_edit = QLineEdit()
        self.sample_edit.setPlaceholderText("до 15 симв.: A-Za-z 0-9 . _ -")
        self.sample_edit.setMaxLength(15)
        self.sample_edit.setValidator(
            QRegularExpressionValidator(QRegularExpression("^[A-Za-z0-9._-]{0,15}$"))
        )
        form.addRow("Образец", self.sample_edit)

        self.v1_spin = QDoubleSpinBox()
        self.v1_spin.setRange(V1_MIN, V1_MAX)
        self.v1_spin.setDecimals(3)
        self.v1_spin.setSingleStep(0.1)
        self.v1_spin.setSuffix(" В")
        self.v1_spin.setValue(-1.000)
        form.addRow("V1 (начало)", self.v1_spin)

        self.v2_spin = QDoubleSpinBox()
        self.v2_spin.setRange(V2_MIN, V2_MAX)
        self.v2_spin.setDecimals(3)
        self.v2_spin.setSingleStep(0.1)
        self.v2_spin.setSuffix(" В")
        self.v2_spin.setValue(1.000)
        form.addRow("V2 (конец)", self.v2_spin)

        self.vs_spin = QDoubleSpinBox()
        self.vs_spin.setRange(0.001, 2.0)
        self.vs_spin.setDecimals(3)
        self.vs_spin.setSingleStep(0.05)
        self.vs_spin.setSuffix(" В")
        self.vs_spin.setValue(0.1)
        form.addRow("Шаг Vs", self.vs_spin)

        self.dt_spin = QDoubleSpinBox()
        self.dt_spin.setRange(0.0, 60.0)
        self.dt_spin.setDecimals(3)
        self.dt_spin.setSingleStep(0.05)
        self.dt_spin.setSuffix(" с")
        self.dt_spin.setValue(0.0)
        form.addRow("Δt", self.dt_spin)

        self.i_max_spin = QDoubleSpinBox()
        self.i_max_spin.setRange(I_MAX_MIN, I_MAX_MAX)
        self.i_max_spin.setDecimals(9)
        self.i_max_spin.setSingleStep(0.001)
        self.i_max_spin.setSuffix(" А")
        self.i_max_spin.setValue(0.01)
        form.addRow("I_max", self.i_max_spin)

        layout.addLayout(form)

        chip_row = QHBoxLayout()
        chip_row.addWidget(QLabel("Тип чипа:"))
        self.radio_op2 = QRadioButton("OP2")
        self.radio_md2 = QRadioButton("MD2")
        self._chip_group = QButtonGroup(self)
        self._chip_group.addButton(self.radio_op2)
        self._chip_group.addButton(self.radio_md2)
        self._chip_group.buttonClicked.connect(self._on_chip_clicked)
        chip_row.addWidget(self.radio_op2)
        chip_row.addWidget(self.radio_md2)
        chip_row.addStretch(1)
        layout.addLayout(chip_row)

        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel("Масштаб:"))
        self.radio_lin = QRadioButton("лин")
        self.radio_log = QRadioButton("лог")
        self.radio_lin.setChecked(True)
        self._scale_group = QButtonGroup(self)
        self._scale_group.addButton(self.radio_lin)
        self._scale_group.addButton(self.radio_log)
        self._scale_group.buttonClicked.connect(self._on_scale_clicked)
        scale_row.addWidget(self.radio_lin)
        scale_row.addWidget(self.radio_log)
        scale_row.addStretch(1)
        layout.addLayout(scale_row)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: red;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        buttons = QHBoxLayout()
        self.start_button = QPushButton("start")
        self.stop_button = QPushButton("stop")
        self.start_button.clicked.connect(self._on_start_clicked)
        self.stop_button.clicked.connect(self._on_stop_clicked)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        layout.addLayout(buttons)

        default_buttons = QHBoxLayout()
        self.load_default_button = QPushButton("Load default")
        self.set_default_button = QPushButton("Set default")
        self.load_default_button.clicked.connect(self.load_default)
        self.set_default_button.clicked.connect(self.set_default)
        default_buttons.addWidget(self.load_default_button)
        default_buttons.addWidget(self.set_default_button)
        layout.addLayout(default_buttons)
        layout.addStretch(1)

    def _collect_values(self) -> dict | None:
        """Собрать и провалидировать рабочие значения полей (секция values конфига)."""
        sample = self.sample_edit.text()
        if not sample or not sample.strip("."):
            self._show_error("Введите номер образца (латиница, цифры, . _ -; до 15 символов).")
            return None

        if self.radio_op2.isChecked():
            chip_type = "OP2"
        elif self.radio_md2.isChecked():
            chip_type = "MD2"
        else:
            self._show_error("Выберите тип чипа (OP2 или MD2).")
            return None

        values = {
            "sample": sample,
            "V1": self.v1_spin.value(),
            "V2": self.v2_spin.value(),
            "Vs": self.vs_spin.value(),
            "dt": self.dt_spin.value(),
            "chipType": chip_type,
            "i_max": self.i_max_spin.value(),
            "scale": "log" if self.radio_log.isChecked() else "lin",
        }
        if self._ps_widget is not None:
            a, b = self._ps_widget.get_contacts()
            values["contactA"] = a
            values["contactB"] = b
        return values

    def _collect_params(self) -> dict | None:
        """Собрать dict параметров для sig_start: values + папка запуска и лимиты."""
        params = self._collect_values()
        if params is None:
            return None
        if self._session is not None and self._session.dir:
            params["session_dir"] = self._session.dir
            params["limits"] = self._session.read_config()["limits"]
        return params

    def set_default(self) -> None:
        """Кнопка «Set default»: выгрузить поля GUI в config.json (лимиты сохраняются)."""
        values = self._collect_values()
        if values is not None and self._session is not None:
            self._session.write_values(values)
            self._clear_error()

    def load_default(self) -> None:
        """Кнопка «Load default»: подгрузить values и limits из config.json в GUI."""
        if self._session is not None:
            self.apply_config(self._session.read_config())
            self._clear_error()

    def apply_config(self, cfg: dict) -> None:
        """Применить конфиг: limits — в диапазоны спинбоксов, values — в поля."""
        limits, values = cfg["limits"], cfg["values"]
        self.v1_spin.setRange(limits["V_min"], -0.001)
        self.v2_spin.setRange(0.001, limits["V_max"])
        self.vs_spin.setRange(limits["Vs_min"], limits["Vs_max"])
        self.dt_spin.setRange(limits["dt_min"], limits["dt_max"])
        self.i_max_spin.setRange(1e-9, limits["i_max_limit"])

        self.sample_edit.setText(str(values["sample"]))
        self.v1_spin.setValue(float(values["V1"]))
        self.v2_spin.setValue(float(values["V2"]))
        self.vs_spin.setValue(float(values["Vs"]))
        self.dt_spin.setValue(float(values["dt"]))
        self.i_max_spin.setValue(float(values["i_max"]))

        chip = values.get("chipType")
        if chip in ("MD2", "OP2"):
            (self.radio_md2 if chip == "MD2" else self.radio_op2).setChecked(True)
            self.chipTypeChanged.emit(chip)
            if self._ps_widget is not None:
                self._ps_widget.set_contacts(int(values["contactA"]),
                                             int(values["contactB"]))

        log_scale = values.get("scale") == "log"
        (self.radio_log if log_scale else self.radio_lin).setChecked(True)
        self.scaleChanged.emit(log_scale)

    def _on_start_clicked(self) -> None:
        """Кнопка «start» по кругу: start -> pause -> resume -> pause ..."""
        self._clear_error()
        if self._state == "idle":
            params = self._collect_params()
            if params is not None:
                self.sig_start.emit(params)
        elif self._state == "started":
            self.sig_pause.emit()
        elif self._state == "paused":
            self.sig_resume.emit()

    def _on_stop_clicked(self) -> None:
        """Кнопка «stop»: остановить измерение."""
        self.sig_stop.emit()

    def _on_chip_clicked(self, button) -> None:
        self.chipTypeChanged.emit(button.text())

    def _on_scale_clicked(self, button) -> None:
        self.scaleChanged.emit(button.text() == "лог")

    def on_started(self) -> None:
        """Измерение запущено: заблокировать все поля ввода."""
        self._state = "started"
        self.start_button.setText("pause")
        self._set_inputs_enabled(False)

    def on_paused(self) -> None:
        self._state = "paused"
        self.start_button.setText("resume")

    def on_resumed(self) -> None:
        self._state = "started"
        self.start_button.setText("pause")

    def on_stopped(self) -> None:
        """Измерение остановлено: разблокировать поля, вернуть кнопку в «start»."""
        self._state = "idle"
        self.start_button.setText("start")
        self._set_inputs_enabled(True)

    def on_start_failed(self, reason: str) -> None:
        """Старт отклонён (контроллер): показать причину, остаться в idle."""
        self._state = "idle"
        self.start_button.setText("start")
        self._set_inputs_enabled(True)
        self._show_error(reason)

    def _set_inputs_enabled(self, enabled: bool) -> None:
        """Блокировка/разблокировка всех полей ввода."""
        for w in (
            self.sample_edit,
            self.v1_spin,
            self.v2_spin,
            self.vs_spin,
            self.dt_spin,
            self.i_max_spin,
            self.radio_op2,
            self.radio_md2,
            self.load_default_button,
            self.set_default_button,
        ):
            w.setEnabled(enabled)
        if self._ps_widget is not None:
            self._ps_widget.set_enabled(enabled)

    def _show_error(self, text: str) -> None:
        self.status_label.setText(text)

    def _clear_error(self) -> None:
        self.status_label.setText("")
