"""
Виджет задания базовых параметров измерения (controller_widget).

По ТЗ виджет отрисован программно (без .ui), весь обмен — через сигналы/слоты:

Поля ввода (порядок — как на схеме ментора Test/shema_asp.jpg):
- номер образца (sample): латиница и цифры; прочее при формировании имени
  файла вычищается (решение автора ТЗ);
- V1 — напряжение начала развёртки: жёсткий предел [-1.000; 0) В,
  по умолчанию -1.000 В (по схеме ментора). Предел зашит в спинбокс —
  «в поле поставить нельзя» (защита образца от выгорания);
- V2 — напряжение конца: жёсткий предел (0; +1.000] В, по умолчанию +1.000 В;
- Vs — шаг напряжения, В (положительное дробное, пример на схеме 0.1 В);
- Δt — временной шаг, с (выдержка после установки напряжения; по умолчанию 0);
- compliance — ограничение тока, А: по умолчанию 0.01 А (10 мА),
  допустимый диапазон 1 нА … 100 мА. Выставляется на приборе при старте
  ДО включения output;
- тип чипа OP2/MD2 — радиокнопки, по умолчанию НИЧЕГО не выбрано; при смене
  эмитится chipTypeChanged(str) (слушает ps_widget);
- масштаб графика лин/лог — радиокнопки; при смене эмитится scaleChanged(bool)
  (True = логарифмический; слушает graph.py).

Кнопки (рядом, шириной как ячейки ввода):
- «start»: при нажатии превращается в «pause», тот в «resume» и так по кругу,
  пока измерение не сброшено;
- «restart»: останавливает процессы, сбрасывает состояние, шлёт сигнал
  на сохранение данных (sig_stop -> controller.stop сохраняет JSON+CSV).

Сигналы (все команды — в контроллер):
  sig_start(dict: sample, V1, V2, Vs, dt, chipType, compliance, contactA, contactB),
  sig_pause(), sig_resume(), sig_stop().
Межвиджетные: chipTypeChanged(str), scaleChanged(bool).
Слоты статуса от контроллера: on_started, on_paused, on_resumed,
on_stopped, on_start_failed(str).
"""

import re

from PySide6.QtCore import QRegularExpression, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

# Жёсткие технические пределы (решение автора ТЗ — защита образца):
V1_MIN, V1_MAX = -1.000, -0.001   # V1 ∈ [-1.000; 0) В
V2_MIN, V2_MAX = 0.001, 1.000     # V2 ∈ (0; +1.000] В
COMP_MIN, COMP_MAX = 1e-9, 0.1    # compliance ∈ [1 нА; 100 мА]


class ControllerWidget(QWidget):
    """Поля параметров измерения + кнопки start/pause/resume и restart."""

    # Команды в контроллер (worker-поток; соединения — в main.py)
    sig_start = Signal(dict)
    sig_pause = Signal()
    sig_resume = Signal()
    sig_stop = Signal()

    # Межвиджетные связи (GUI-поток)
    chipTypeChanged = Signal(str)
    scaleChanged = Signal(bool)

    def __init__(self, ps_widget=None, parent=None):
        """
        :param ps_widget: ссылка на ps_widget — забор выбранных контактов
                          при формировании dict старта. Оба виджета живут
                          в GUI-потоке, прямой вызов get_contacts() между
                          ними безопасен (запрет ТЗ — только для межпоточных
                          вызовов). None допустим: тогда контакты в dict
                          не включаются (режим тестирования виджета отдельно).
        """
        super().__init__(parent)
        self._ps_widget = ps_widget
        # Зеркало состояния конечного автомата контроллера: по нему кнопка
        # «start» решает, какую команду слать. Устанавливается только слотами
        # on_started/on_paused/on_resumed/on_stopped — виджет не угадывает
        # состояние сам, чтобы не рассинхронизироваться с контроллером.
        self._state = "idle"

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Параметры измерения</b>"))

        form = QFormLayout()

        # Номер образца: латиница и цифры в любом регистре. Жёсткое
        # ограничение в поле не критично (решение автора ТЗ) — остальное
        # всё равно вычищается при сборке имени файла.
        self.sample_edit = QLineEdit()
        self.sample_edit.setPlaceholderText("напр. ABC123")
        self.sample_edit.setValidator(
            QRegularExpressionValidator(QRegularExpression("^[A-Za-z0-9]*$"))
        )
        form.addRow("Образец", self.sample_edit)

        # V1: жёсткий предел не ниже -1.000 В и строго ниже 0 —
        # защита от выгорания образца. Спинбокс сам не даст ввести хлам.
        self.v1_spin = QDoubleSpinBox()
        self.v1_spin.setRange(V1_MIN, V1_MAX)
        self.v1_spin.setDecimals(3)
        self.v1_spin.setSingleStep(0.1)
        self.v1_spin.setSuffix(" В")
        self.v1_spin.setValue(-1.000)
        form.addRow("V1 (начало)", self.v1_spin)

        # V2: симметричен V1 — не выше +1.000 В и строго выше 0.
        self.v2_spin = QDoubleSpinBox()
        self.v2_spin.setRange(V2_MIN, V2_MAX)
        self.v2_spin.setDecimals(3)
        self.v2_spin.setSingleStep(0.1)
        self.v2_spin.setSuffix(" В")
        self.v2_spin.setValue(1.000)
        form.addRow("V2 (конец)", self.v2_spin)

        # Шаг напряжения: положительное дробное, пример ментора — 0.1 В.
        self.vs_spin = QDoubleSpinBox()
        self.vs_spin.setRange(0.001, 2.0)
        self.vs_spin.setDecimals(3)
        self.vs_spin.setSingleStep(0.05)
        self.vs_spin.setSuffix(" В")
        self.vs_spin.setValue(0.1)
        form.addRow("Шаг Vs", self.vs_spin)

        # Временной шаг: сколько ждать после установки напряжения,
        # чтобы оно установилось на образце. По умолчанию 0 (решение автора).
        self.dt_spin = QDoubleSpinBox()
        self.dt_spin.setRange(0.0, 60.0)
        self.dt_spin.setDecimals(3)
        self.dt_spin.setSingleStep(0.05)
        self.dt_spin.setSuffix(" с")
        self.dt_spin.setValue(0.0)
        form.addRow("Δt", self.dt_spin)

        # Compliance: ограничение тока, защита образца. Диапазон 1 нА..100 мА;
        # decimals=9, чтобы можно было ввести наноамперные значения.
        self.comp_spin = QDoubleSpinBox()
        self.comp_spin.setRange(COMP_MIN, COMP_MAX)
        self.comp_spin.setDecimals(9)
        self.comp_spin.setSingleStep(0.001)
        self.comp_spin.setSuffix(" А")
        self.comp_spin.setValue(0.01)   # 10 мА по умолчанию
        form.addRow("Compliance", self.comp_spin)

        layout.addLayout(form)

        # Тип чипа: по ТЗ никакой вариант не выбран по умолчанию.
        # Ячейки контактов (ps_widget) недоступны, пока тип не выбран.
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

        # Масштаб графика: линейный/логарифмический (ток в лог-режиме —
        # log|I|, см. graph.py: ветви развёртки бывают отрицательными).
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

        # Строка состояния для причин отказа в старте: неблокирующий QLabel
        # вместо QMessageBox — модальный диалог в headless-прогоне зависнет.
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: red;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Кнопки start/restart: рядом друг с другом, шириной как ячейки
        # ввода (решение автора ТЗ — визуальное единство колонки).
        buttons = QHBoxLayout()
        self.start_button = QPushButton("start")
        self.restart_button = QPushButton("restart")
        self.start_button.clicked.connect(self._on_start_clicked)
        self.restart_button.clicked.connect(self._on_restart_clicked)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.restart_button)
        layout.addLayout(buttons)
        layout.addStretch(1)

    # ------------------------------------------------------------------ #
    # Формирование параметров старта                                     #
    # ------------------------------------------------------------------ #

    def _collect_params(self) -> dict | None:
        """Собрать и провалидировать dict параметров для sig_start.

        Возвращает None и показывает причину в status_label, если параметры
        неполные (пустой образец / не выбран тип чипа). Числовые пределы
        гарантированы спинбоксами, здесь — только то, что полями не ловится.
        """
        # Вычищаем всё, кроме латиницы и цифр (решение автора ТЗ): имя файла
        # должно быть безопасным на любой ФС, даже если валидатор обойдут.
        sample = re.sub(r"[^A-Za-z0-9]", "", self.sample_edit.text())
        if not sample:
            self._show_error("Введите номер образца (латиница и цифры).")
            return None

        if self.radio_op2.isChecked():
            chip_type = "OP2"
        elif self.radio_md2.isChecked():
            chip_type = "MD2"
        else:
            self._show_error("Выберите тип чипа (OP2 или MD2).")
            return None

        params = {
            "sample": sample,
            "V1": self.v1_spin.value(),
            "V2": self.v2_spin.value(),
            "Vs": self.vs_spin.value(),
            "dt": self.dt_spin.value(),
            "chipType": chip_type,
            "compliance": self.comp_spin.value(),
        }
        if self._ps_widget is not None:
            a, b = self._ps_widget.get_contacts()
            params["contactA"] = a
            params["contactB"] = b
        return params

    # ------------------------------------------------------------------ #
    # Обработчики кнопок                                                 #
    # ------------------------------------------------------------------ #

    def _on_start_clicked(self) -> None:
        """Кнопка «start» по кругу: start -> pause -> resume -> pause ..."""
        self._clear_error()
        if self._state == "idle":
            params = self._collect_params()
            if params is not None:
                self.sig_start.emit(params)
            # Дальнейшая смена подписи/блокировка — только слотом on_started
            # после подтверждения контроллером. Так при отказе старта
            # (startFailed) виджет остаётся в исходном состоянии сам.
        elif self._state == "started":
            self.sig_pause.emit()
        elif self._state == "paused":
            self.sig_resume.emit()

    def _on_restart_clicked(self) -> None:
        """Кнопка «restart»: остановить, сбросить состояние, сохранить данные.

        Сигнал идёт в контроллер (sig_stop); фактическая разблокировка полей
        и возврат кнопки в «start» происходят слотом on_stopped по stopDone.
        """
        self.sig_stop.emit()

    # ------------------------------------------------------------------ #
    # Межвиджетные эмиссии                                               #
    # ------------------------------------------------------------------ #

    def _on_chip_clicked(self, button) -> None:
        # ps_widget подписан на этот сигнал и обновляет диапазон контактов;
        # ни один вариант не выбран по умолчанию (по ТЗ).
        self.chipTypeChanged.emit(button.text())

    def _on_scale_clicked(self, button) -> None:
        # graph.py подписан на этот сигнал; True = логарифмический масштаб.
        self.scaleChanged.emit(button.text() == "лог")

    # ------------------------------------------------------------------ #
    # Слоты статуса от контроллера                                       #
    # ------------------------------------------------------------------ #

    def on_started(self) -> None:
        """Измерение запущено: блокировать ВСЕ поля ввода (кроме самой
        кнопки и «restart») — иначе параметры можно поменять посреди
        развёртки и получить файл данных, не соответствующий измерению."""
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
        """Измерение остановлено (restart или естественный конец): разблокировать
        поля, кнопка возвращается в «start»."""
        self._state = "idle"
        self.start_button.setText("start")
        self._set_inputs_enabled(True)

    def on_start_failed(self, reason: str) -> None:
        """Старт отклонён (контроллер): показать причину, остаться в idle."""
        self._state = "idle"
        self.start_button.setText("start")
        self._set_inputs_enabled(True)
        self._show_error(reason)

    # ------------------------------------------------------------------ #
    # Вспомогательное                                                      #
    # ------------------------------------------------------------------ #

    def _set_inputs_enabled(self, enabled: bool) -> None:
        """Блокировка/разблокировка всех полей ввода.

        На паузе поля НЕ разблокируются — менять параметры можно только
        в idle, иначе файл данных перестанет соответствовать измерению.
        """
        for w in (
            self.sample_edit,
            self.v1_spin,
            self.v2_spin,
            self.vs_spin,
            self.dt_spin,
            self.comp_spin,
            self.radio_op2,
            self.radio_md2,
            self.radio_lin,
            self.radio_log,
        ):
            w.setEnabled(enabled)
        # Контакты тоже поле ввода: разводить реле посреди развёртки нельзя.
        if self._ps_widget is not None:
            self._ps_widget.set_enabled(enabled)

    def _show_error(self, text: str) -> None:
        self.status_label.setText(text)

    def _clear_error(self) -> None:
        self.status_label.setText("")
