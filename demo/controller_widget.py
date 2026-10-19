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
    """Поля параметров измерения + кнопки start/pause/resume и stop.

    Значения и лимиты полей задаются активным конфигом (config.json рабочей
    папки или local_config.json образца): подтягиваются при старте, по
    сигналу session.configChanged (правки файла из консоли) и при выборе
    образца с существующим local_config. Из GUI в JSON не пишется ничего.
    """

    sig_start = Signal(dict)
    sig_pause = Signal()
    sig_resume = Signal()
    sig_stop = Signal()

    chipTypeChanged = Signal(str)
    scaleChanged = Signal(bool)

    def __init__(self, ps_widget=None, session=None, parent=None):
        """Создать поля ввода и кнопки; ps_widget даёт контакты, session — конфиги."""
        super().__init__(parent)
        self._ps_widget = ps_widget
        self._session = session
        self._state = "idle"
        self._pending_config: dict | None = None

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.sample_edit = QLineEdit()
        self.sample_edit.setPlaceholderText("до 15 симв.: A-Za-z 0-9 . _ -")
        self.sample_edit.setMaxLength(15)
        self.sample_edit.setValidator(
            QRegularExpressionValidator(QRegularExpression("^[A-Za-z0-9._-]{0,15}$"))
        )
        self.sample_edit.textChanged.connect(self._on_sample_changed)
        form.addRow("Образец", self.sample_edit)

        self.v1_spin = QDoubleSpinBox()
        self.v1_spin.setRange(V1_MIN, V1_MAX)
        self.v1_spin.setDecimals(3)
        self.v1_spin.setSuffix(" В")
        self.v1_spin.setValue(-1.000)
        form.addRow("V1 (начало)", self.v1_spin)

        self.v2_spin = QDoubleSpinBox()
        self.v2_spin.setRange(V2_MIN, V2_MAX)
        self.v2_spin.setDecimals(3)
        self.v2_spin.setSuffix(" В")
        self.v2_spin.setValue(1.000)
        form.addRow("V2 (конец)", self.v2_spin)

        self.vs_spin = QDoubleSpinBox()
        self.vs_spin.setRange(0.001, 2.0)
        self.vs_spin.setDecimals(3)
        self.vs_spin.setSingleStep(0.05)
        self.vs_spin.setSuffix(" В")
        self.vs_spin.setValue(0.1)
        self.vs_spin.valueChanged.connect(self._on_vs_changed)
        form.addRow("Шаг Vs", self.vs_spin)
        self._on_vs_changed(self.vs_spin.value())

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
        layout.addStretch(1)

        self._update_buttons()

    def _collect_values(self) -> dict | None:
        """Собрать и провалидировать рабочие значения полей."""
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
        """Собрать dict параметров для sig_start: values + папка образца и лимиты.

        Папка образца (<workdir>/<sample>/) создаётся здесь — при нажатии start.
        """
        params = self._collect_values()
        if params is None:
            return None
        if self._session is not None:
            params["session_dir"] = self._session.sample_dir(params["sample"])
            params["limits"] = self._session.read_config()["limits"]
        return params

    def _init_from_config(self) -> None:
        """Применить активный конфиг при запуске приложения."""
        if self._session is not None:
            self.apply_config(self._session.read_config())

    def apply_config_safe(self, cfg: dict) -> None:
        """Слот для session.configChanged: применить в idle, иначе — после stop."""
        if self._state == "idle":
            self.apply_config(cfg)
        else:
            self._pending_config = cfg

    def apply_config(self, cfg: dict) -> None:
        """Применить конфиг: limits — в диапазоны спинбоксов, values — в поля.

        Поле «Образец» конфигом не управляется (sample в конфигах нет).
        """
        limits, values = cfg["limits"], cfg["values"]
        self.v1_spin.setRange(limits["V_min"], -0.001)
        self.v2_spin.setRange(0.001, limits["V_max"])
        self.vs_spin.setRange(limits["Vs_min"], limits["Vs_max"])
        self.dt_spin.setRange(limits["dt_min"], limits["dt_max"])
        self.i_max_spin.setRange(1e-9, limits["i_max_limit"])

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

    def _on_vs_changed(self, vs: float) -> None:
        """Шаг стрелок V1/V2 равен Vs: изменения кратны шагу развёртки."""
        self.v1_spin.setSingleStep(vs)
        self.v2_spin.setSingleStep(vs)

    def _on_sample_changed(self, sample: str) -> None:
        """Смена образца: доступность start + local_config образца, если он есть."""
        self._update_buttons()
        if self._state != "idle" or self._session is None:
            return
        local = self._session.local_config_path(sample) if sample else None
        self._session.set_active_config(local)
        if local is not None:
            self.apply_config(self._session.read_config(local))

    def on_started(self) -> None:
        """Измерение запущено: заблокировать поля, watcher — на local_config образца."""
        self._state = "started"
        self.start_button.setText("pause")
        self._set_inputs_enabled(False)
        self._update_buttons()
        if self._session is not None:
            local = self._session.local_config_path(self.sample_edit.text())
            if local is not None:
                self._session.set_active_config(local)

    def on_paused(self) -> None:
        self._state = "paused"
        self.start_button.setText("resume")
        self._update_buttons()

    def on_resumed(self) -> None:
        self._state = "started"
        self.start_button.setText("pause")
        self._update_buttons()

    def on_stopped(self) -> None:
        """Измерение остановлено: разблокировать поля, вернуть кнопку в «start»."""
        self._state = "idle"
        self.start_button.setText("start")
        self._set_inputs_enabled(True)
        self._update_buttons()
        if self._pending_config is not None:
            cfg, self._pending_config = self._pending_config, None
            self.apply_config(cfg)

    def on_start_failed(self, reason: str) -> None:
        """Старт отклонён (контроллер): показать причину, остаться в idle."""
        self._state = "idle"
        self.start_button.setText("start")
        self._set_inputs_enabled(True)
        self._update_buttons()
        self._show_error(reason)

    def _update_buttons(self) -> None:
        """start неактивен без имени образца; stop — только вне idle."""
        self.start_button.setEnabled(
            self._state != "idle" or bool(self.sample_edit.text()))
        self.stop_button.setEnabled(self._state != "idle")

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
        ):
            w.setEnabled(enabled)
        if self._ps_widget is not None:
            self._ps_widget.set_enabled(enabled)

    def _show_error(self, text: str) -> None:
        self.status_label.setText(text)

    def _clear_error(self) -> None:
        self.status_label.setText("")
