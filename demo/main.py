import json
import sys

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtWidgets import QApplication

import theme
from K2636B import K2636B
from PS import PS
from MainWindow import MainWindow
from K2636B_widget import ActualIV
from controller import Controller
from controller_widget import ControllerWidget
from graph_widget import Graph
from history_widget import History
from PS_widget import PSWidget
from session import Session

QC = Qt.ConnectionType.QueuedConnection


def build_application() -> tuple:
    """Создать приложение, потоки, объекты и окно, не запуская цикл событий."""
    app = QApplication(sys.argv)
    app.setApplicationName("Automated Probe Station")
    font = app.font()
    font.setPointSize(12)
    app.setFont(font)

    deviceThread = QThread()
    deviceThread.setObjectName("deviceThread")

    k2636b = K2636B(debug=False)
    ps = PS(debug=False)
    controller = Controller(k2636b, ps)

    for obj in (controller, k2636b, ps):
        obj.moveToThread(deviceThread)

    graph = Graph()
    ps_widget = PSWidget()
    session = Session()
    session.start_run()
    controller_widget = ControllerWidget(ps_widget=ps_widget, session=session)
    actual_iv = ActualIV()
    history = History(session=session)
    window = MainWindow(graph, controller_widget, actual_iv, ps_widget, history,
                        deviceThread)

    wire(app, controller, k2636b, ps, window, graph, history,
         controller_widget, actual_iv, ps_widget, deviceThread)

    controller_widget.load_default()

    deviceThread.finished.connect(controller.deleteLater)
    deviceThread.finished.connect(k2636b.deleteLater)
    deviceThread.finished.connect(ps.deleteLater)

    deviceThread.start()

    QTimer.singleShot(0, controller.startup.emit)

    return app, window, controller, k2636b, ps, deviceThread


def wire(app, controller, k2636b, ps, window, graph, history,
         controller_widget, actual_iv, ps_widget, deviceThread) -> None:
    """Все connect() приложения — в одном месте."""
    controller_widget.sig_start.connect(controller.start, QC)
    controller_widget.sig_pause.connect(controller.pause, QC)
    controller_widget.sig_resume.connect(controller.resume, QC)
    controller_widget.sig_stop.connect(controller.stop, QC)
    actual_iv.channelChanged.connect(controller.set_channel, QC)

    controller.startDone.connect(controller_widget.on_started, QC)
    controller.startFailed.connect(controller_widget.on_start_failed, QC)
    controller.pauseDone.connect(controller_widget.on_paused, QC)
    controller.resumeDone.connect(controller_widget.on_resumed, QC)
    controller.stopDone.connect(controller_widget.on_stopped, QC)
    controller.pointUpdated.connect(graph.add_point, QC)
    controller.progressUpdated.connect(controller_widget.progress_bar.setValue, QC)
    controller.startDone.connect(graph.clear, QC)
    controller.startDone.connect(actual_iv.lock, QC)
    controller.stopDone.connect(actual_iv.unlock, QC)
    controller.startFailed.connect(actual_iv.unlock, QC)

    controller.stopDone.connect(history.add_file, QC)

    def on_curve_toggled(path: str, on: bool, color) -> None:
        """Галочка истории: построить кривую из .vag или снять её с графика."""
        if not on:
            graph.remove_history_curve(path)
            return
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            graph.add_history_curve(path, d["u"], d["i"], color)
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"# main: не удалось прочитать {path}: {e}")
            history.mark_error(path)

    history.curveToggled.connect(on_curve_toggled)

    k2636b.newIV_A.connect(actual_iv.on_iv_A, QC)
    k2636b.newIV_B.connect(actual_iv.on_iv_B, QC)

    k2636b.connectionLost.connect(controller.on_connection_lost, QC)
    k2636b.error.connect(controller.on_device_error, QC)

    controller.startup.connect(controller.initialize, QC)

    controller_widget.chipTypeChanged.connect(ps_widget.set_chip_type)
    controller_widget.scaleChanged.connect(graph.set_log_scale)

    window.shutdownRequested.connect(controller.shutdown, QC)
    controller.shutdownDone.connect(window.on_shutdown_done, QC)

    def apply_theme(name: str) -> None:
        app.setStyleSheet(theme.qss(name))
        graph.apply_theme(theme.graph_colors(name))

    window.themeChanged.connect(apply_theme)
    apply_theme(window.theme_combo.currentText())


def main() -> int:
    app, window, controller, k2636b, ps, deviceThread = build_application()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
