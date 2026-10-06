from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from theme import THEMES


class MainWindow(QMainWindow):
    """Окно: график слева, столбец виджетов справа; каждый виджет — в рамке QGroupBox."""

    shutdownRequested = Signal()
    themeChanged = Signal(str)

    def __init__(self, graph, controller_widget, actual_iv, ps_widget, history,
                 deviceThread, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Автоматизированная зондовая станция — ВАХ")
        self._deviceThread = deviceThread
        self._shutdown_accepted = False
        self._shutdown_requested = False

        theme_row = QWidget()
        theme_layout = QHBoxLayout(theme_row)
        theme_layout.setContentsMargins(0, 0, 0, 0)
        theme_layout.addWidget(QLabel("Тема:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(THEMES)
        self.theme_combo.currentTextChanged.connect(self.themeChanged)
        theme_layout.addWidget(self.theme_combo)
        theme_layout.addStretch(1)

        column = QWidget()
        column_layout = QVBoxLayout(column)
        column_layout.addWidget(self._boxed("Параметры измерения", controller_widget))
        column_layout.addWidget(self._boxed("Текущие значения", actual_iv))
        column_layout.addWidget(self._boxed("Контакты зондовой станции", ps_widget))
        column_layout.addWidget(self._boxed("История измерений", history), stretch=1)
        column_layout.addWidget(self._boxed("Оформление", theme_row))
        column.setMinimumWidth(280)

        graph.setMinimumSize(500, 400)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._boxed("ВАХ", graph))
        splitter.addWidget(column)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([750, 250])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        self.setCentralWidget(splitter)

        self.resize(1024, 640)

    @staticmethod
    def _boxed(title: str, widget: QWidget) -> QGroupBox:
        """Виджет в рамке с заголовком."""
        box = QGroupBox(title)
        box_layout = QVBoxLayout(box)
        box_layout.addWidget(widget)
        return box

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
