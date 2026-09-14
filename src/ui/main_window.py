"""Главное окно: каркас сигналов, поток и временная панель.

Здесь собирается вся «распайка» архитектуры:

* создаётся QThread, в него переносятся драйверы приборов и контроллер;
* сигналы GUI (sig_start/sig_pause/sig_resume/sig_stop) коннектятся
  к слотам контроллера;
* сигналы контроллера и приборов коннектятся к слотам GUI
  (живые поля u/I, график, состояние кнопок).

Временная панель ниже — заглушка для отладки связей. Когда интерфейс
будет нарисован в Qt Designer, её заменит сгенерированный ui_mainWindow,
а имена объектов в .ui совпадут с полями (sample_edit, v1_edit, ...).
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QFormLayout, QHBoxLayout, QVBoxLayout,
    QLineEdit, QPushButton, QLabel, QDoubleSpinBox, QSpinBox,
)
from PySide6.QtCore import QThread, Signal, Slot, QReadLocker

from core.devices import K2636B, ProbeStation
from core.controller import SweepController, ContactCheckController


class MainWindow(QMainWindow):
    """Объект GUI: владеет потоком, приборами и контроллером."""

    # GUI -> контроллер
    sig_start = Signal(dict)
    sig_pause = Signal()
    sig_resume = Signal()
    sig_stop = Signal()
    sig_test_start = Signal()
    sig_test_stop = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Automated Probe Station")

        # --- объекты по схеме shema_asp ---
        self.k2636b = K2636B(resource="/dev/usbtmc0")
        self.probe = ProbeStation(resource="/dev/ttyUSB0")
        self.controller = SweepController(self.k2636b, self.probe)
        self.tester = ContactCheckController(self.probe, sweep=self.controller)

        # --- worker-поток: приборы и контроллер живут вне GUI ---
        self.deviceThread = QThread(self)
        self.k2636b.moveToThread(self.deviceThread)
        self.probe.moveToThread(self.deviceThread)
        self.controller.moveToThread(self.deviceThread)
        self.tester.moveToThread(self.deviceThread)

        # --- распайка: GUI -> контроллер ---
        self.sig_start.connect(self.controller.start)
        self.sig_pause.connect(self.controller.pause)
        self.sig_resume.connect(self.controller.resume)
        self.sig_stop.connect(self.controller.stop)
        self.sig_test_start.connect(self.tester.start)
        self.sig_test_stop.connect(self.tester.stop)

        # --- распайка: контроллер/приборы -> GUI ---
        self.controller.pointUpdated.connect(self.on_point)
        self.controller.dataChanged.connect(self.on_data_changed)
        self.controller.startDone.connect(self.on_started)
        self.controller.startFailed.connect(self.on_start_failed)
        self.controller.stopDone.connect(self.on_stopped)
        self.tester.testStarted.connect(self.on_test_started)
        self.tester.testProgress.connect(self.on_test_progress)
        self.tester.testDone.connect(self.on_test_done)
        self.tester.testRefused.connect(self.on_hw_error)
        self.probe.error.connect(self.on_hw_error)

        self._build_stub_ui()
        self.deviceThread.start()

    # ---------- временный интерфейс (заменится на .ui из Designer) ----------

    def _build_stub_ui(self):
        self.sample_edit = QLineEdit("ABC")
        self.v1_edit = QDoubleSpinBox(minimum=-100, maximum=100, value=-1.0)
        self.v2_edit = QDoubleSpinBox(minimum=-100, maximum=100, value=1.0)
        self.vs_edit = QDoubleSpinBox(minimum=1e-4, maximum=100, value=0.1)
        self.dt_edit = QDoubleSpinBox(minimum=0.0, maximum=100, value=0.1)
        # контакты релейной матрицы: по 15 на каждой стороне
        self.a_spin = QSpinBox(minimum=1, maximum=15, value=1)
        self.b_spin = QSpinBox(minimum=1, maximum=15, value=1)

        form = QFormLayout()
        form.addRow("Sample", self.sample_edit)
        form.addRow("V1, V", self.v1_edit)
        form.addRow("V2, V", self.v2_edit)
        form.addRow("Vs, V", self.vs_edit)
        form.addRow("dt, s", self.dt_edit)
        form.addRow("A (1-15)", self.a_spin)
        form.addRow("B (1-15)", self.b_spin)

        self.start_button = QPushButton("▶")
        self.pause_button = QPushButton("⏸")
        self.stop_button = QPushButton("⏹")
        self.start_button.clicked.connect(self.on_start_clicked)
        self.pause_button.clicked.connect(self.sig_pause)
        self.stop_button.clicked.connect(self.sig_stop)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.pause_button)
        buttons.addWidget(self.stop_button)

        self.test_button = QPushButton("Проверить контакты")
        self.test_stop_button = QPushButton("⏹ тест")
        self.test_button.clicked.connect(self.sig_test_start)
        self.test_stop_button.clicked.connect(self.sig_test_stop)
        test_buttons = QHBoxLayout()
        test_buttons.addWidget(self.test_button)
        test_buttons.addWidget(self.test_stop_button)

        self.u_label = QLabel("u = —")
        self.i_label = QLabel("I = —")
        self.points_label = QLabel("точек: 0")
        self.status_label = QLabel("idle")

        left = QVBoxLayout()
        left.addLayout(form)
        left.addLayout(buttons)
        left.addLayout(test_buttons)
        left.addWidget(self.u_label)
        left.addWidget(self.i_label)
        left.addWidget(self.points_label)
        left.addWidget(self.status_label)

        self.plot_stub = QLabel("здесь будет график ВАХ (pyqtgraph)")
        layout = QHBoxLayout()
        layout.addLayout(left)
        layout.addWidget(self.plot_stub, stretch=1)
        central = QWidget(self)
        central.setLayout(layout)
        self.setCentralWidget(central)

    # ---------- слоты GUI ----------

    def on_start_clicked(self):
        self.sig_start.emit({
            "sample": self.sample_edit.text(),
            "V1": self.v1_edit.value(),
            "V2": self.v2_edit.value(),
            "Vs": self.vs_edit.value(),
            "dt": self.dt_edit.value(),
            "contactA": self.a_spin.value(),
            "contactB": self.b_spin.value(),
        })

    @Slot(float, float)
    def on_point(self, u: float, i: float):
        self.u_label.setText(f"u = {u:.4f} V")
        self.i_label.setText(f"I = {i:.4e} A")

    @Slot()
    def on_data_changed(self):
        with QReadLocker(self.controller.eLock):
            n = len(self.controller.e["x"])
        self.points_label.setText(f"точек: {n}")
        # здесь позже: self.plot.updateData(self.controller.e)

    @Slot()
    def on_started(self):
        self.status_label.setText("running")

    @Slot(str)
    def on_start_failed(self, reason: str):
        self.status_label.setText(f"запуск отклонён: {reason}")

    @Slot(str)
    def on_hw_error(self, message: str):
        self.status_label.setText(f"HW: {message}")

    # --- тест контактов ---

    @Slot()
    def on_test_started(self):
        self.status_label.setText("тест контактов: пошёл")

    @Slot(int, int, bool)
    def on_test_progress(self, a: int, b: int, ok: bool):
        # живые значения A/B в полях следуют за текущей парой теста
        self.a_spin.blockSignals(True)
        self.b_spin.blockSignals(True)
        self.a_spin.setValue(a)
        self.b_spin.setValue(b)
        self.a_spin.blockSignals(False)
        self.b_spin.blockSignals(False)
        self.status_label.setText(
            f"тест контактов: пара A={a}, B={b} — {'ok' if ok else 'СБОЙ'}")

    @Slot(bool, int)
    def on_test_done(self, aborted: bool, n_checked: int):
        ending = "прерван" if aborted else "завершён"
        self.status_label.setText(f"тест контактов {ending}: {n_checked}/15 пар")

    @Slot(str)
    def on_stopped(self, path: str):
        self.status_label.setText(f"stopped, сохранено: {path or '—'}")

    # ---------- завершение ----------

    def closeEvent(self, event):
        """Остановить эксперимент и тест, потом поток, потом закрыть приборы."""
        self.sig_stop.emit()
        self.sig_test_stop.emit()
        self.deviceThread.quit()
        self.deviceThread.wait(3000)
        self.k2636b.close()
        self.probe.close()
        super().closeEvent(event)
