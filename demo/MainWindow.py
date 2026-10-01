from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QMainWindow, QSplitter, QVBoxLayout, QWidget


class MainWindow(QMainWindow):
    """Окно с графиком слева и столбцом виджетов справа."""

    shutdownRequested = Signal()

    def __init__(self, graph, controller_widget, actual_iv, ps_widget, deviceThread, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Автоматизированная зондовая станция — ВАХ")
        self._deviceThread = deviceThread
        self._shutdown_accepted = False
        self._shutdown_requested = False

        column = QWidget()
        column_layout = QVBoxLayout(column)
        column_layout.addWidget(controller_widget)
        column_layout.addWidget(actual_iv)
        column_layout.addWidget(ps_widget)
        column_layout.addStretch(1)
        column.setMinimumWidth(280)

        graph.setMinimumSize(500, 400)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(graph)
        splitter.addWidget(column)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([750, 250])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        self.setCentralWidget(splitter)

        self.resize(1024, 640)

    def closeEvent(self, event) -> None:
        """Первый closeEvent — начать асинхронное завершение, повторный — закрыться."""
        if self._shutdown_accepted:
            event.accept()
            return
        event.ignore()
        if not self._shutdown_requested:
            self._shutdown_requested = True
            self.shutdownRequested.emit()

    def on_shutdown_done(self) -> None:
        """Слот завершения: дождаться потока устройства и закрыть окно."""
        deviceThread = self._deviceThread
        deviceThread.quit()
        if not deviceThread.wait(5000):
            print("# main: deviceThread не завершился штатно — terminate()")
            deviceThread.terminate()
            deviceThread.wait(2000)
        self._shutdown_accepted = True
        self.close()
