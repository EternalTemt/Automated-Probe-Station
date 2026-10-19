import argparse
import json
import os
import sys

from PySide6.QtCore import QSettings, Qt, QThread, QTimer
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
from session import DEFAULT_WORKDIR, LOCAL_CONFIG_NAME, Session

QC = Qt.ConnectionType.QueuedConnection


def resolve_workdir() -> str:
    """Рабочая папка: -d (имя — под demo/data/, абсолютный путь — как есть),
    иначе последняя выбранная в GUI (QSettings), иначе demo/data/."""
    parser = argparse.ArgumentParser(description="Автоматизированная зондовая станция")
    parser.add_argument("-d", "--directory", default=None,
                        help="рабочая папка: короткое имя (под demo/data/) или абсолютный путь")
    args, _ = parser.parse_known_args()
    if args.directory:
        if os.path.isabs(args.directory):
            return args.directory
        return os.path.join(DEFAULT_WORKDIR, args.directory)
    saved = QSettings().value("workdir", type=str)
    if saved and os.path.isdir(saved):
        return saved
    return DEFAULT_WORKDIR


def build_application(workdir: str | None = None) -> tuple:
    """Создать приложение, потоки, объекты и окно, не запуская цикл событий.

    workdir=None — разрешить по правилам resolve_workdir(); в тестах папка
    передаётся явно (argparse и QSettings обходятся).
    """
    app = QApplication(sys.argv)
    app.setApplicationName("Automated Probe Station")
    app.setOrganizationName("Automated Probe Station")
    font = app.font()
    font.setPointSize(12)
    app.setFont(font)

    if workdir is None:
        workdir = resolve_workdir()

    deviceThread = QThread()
    deviceThread.setObjectName("deviceThread")

    k2636b = K2636B(debug=False)
    ps = PS(debug=False)
    controller = Controller(k2636b, ps)

    for obj in (controller, k2636b, ps):
        obj.moveToThread(deviceThread)

    graph = Graph()
    ps_widget = PSWidget()
    session = Session(workdir)
    controller_widget = ControllerWidget(ps_widget=ps_widget, session=session)
    actual_iv = ActualIV()
    history = History(workdir=session.workdir)
    window = MainWindow(graph, controller_widget, actual_iv, ps_widget, history,
                        deviceThread)

    wire(app, controller, k2636b, ps, window, graph, history,
         controller_widget, actual_iv, ps_widget, session, deviceThread)

    controller_widget._init_from_config()

    deviceThread.finished.connect(controller.deleteLater)
    deviceThread.finished.connect(k2636b.deleteLater)
    deviceThread.finished.connect(ps.deleteLater)

    deviceThread.start()

    QTimer.singleShot(0, controller.startup.emit)

    return app, window, controller, k2636b, ps, deviceThread


def wire(app, controller, k2636b, ps, window, graph, history,
         controller_widget, actual_iv, ps_widget, session, deviceThread) -> None:
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

    # После остановки: живая кривая очищается, измерение сразу показывается
    # из сохранённого файла (в истории прожимается галочка).
    controller.stopDone.connect(history.on_measurement_saved, QC)
    controller.stopDone.connect(graph.clear, QC)

    def on_curve_toggled(path: str, on: bool, color) -> None:
        """Галочка истории: построить кривую из .vac или снять её с графика."""
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

    def on_primary_changed(dir_path: str) -> None:
        """«Сделать основной»: watcher — на local_config этой папки, если он есть."""
        local = os.path.join(dir_path, LOCAL_CONFIG_NAME)
        session.set_active_config(local if os.path.exists(local) else None)

    history.primaryChanged.connect(on_primary_changed)

    def on_workdir_changed(dir_path: str) -> None:
        """Смена рабочей папки из GUI: session + запомнить в QSettings."""
        session.set_workdir(dir_path)
        QSettings().setValue("workdir", dir_path)

    history.workdirChanged.connect(on_workdir_changed)

    session.configChanged.connect(controller_widget.apply_config_safe)

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
