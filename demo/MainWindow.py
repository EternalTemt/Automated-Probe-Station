"""
Главное окно приложения (MainWindow).

По ТЗ:
- отрисовывает окно и размещает в нём все виджеты: graph.py (слева) и
  столбец справа, сверху вниз (по схеме ментора Test/shema_asp.jpg):
  controller_widget (параметры + кнопки) -> actual_IV (живые u, I) ->
  ps_widget (контакты A, B);
- QSplitter с отношением сторон график : столбец = 3 : 1 по длине,
  соотношение регулируется растягиванием (масштабированием графика), но
  у виджетов есть минимальный размер, меньше которого сделать нельзя
  (чтобы не поломать интерфейс);
- при закрытии окна закрываются все виджеты и программа целиком — по
  процедуре из раздела ТЗ «Завершение программы».

Завершение асинхронное: closeEvent не закрывает окно сразу, а просит
контроллер (сигнал shutdownRequested -> controller.shutdown в worker-потоке)
остановить измерение, снять напряжение и закрыть приборы; по shutdownDone
main.py гасит поток и закрывает окно повторно — уже с accept.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QMainWindow, QSplitter, QVBoxLayout, QWidget


class MainWindow(QMainWindow):
    """Окно с графиком слева и столбцом виджетов справа."""

    # Запрос на завершение программы: main.py соединяет с controller.shutdown
    # (QueuedConnection — слот исполнится в worker-потоке).
    shutdownRequested = Signal()

    def __init__(self, graph, controller_widget, actual_iv, ps_widget, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Автоматизированная зондовая станция — ВАХ")
        self._shutdown_accepted = False   # повторный close() после shutdown — принять
        self._shutdown_requested = False  # запрос уходит в контроллер один раз

        # Столбец виджетов справа (порядок — по схеме ментора).
        column = QWidget()
        column_layout = QVBoxLayout(column)
        column_layout.addWidget(controller_widget)
        column_layout.addWidget(actual_iv)
        column_layout.addWidget(ps_widget)
        column_layout.addStretch(1)
        # Минимальная ширина столбца: меньше — ячейки ввода сжимаются
        # до нечитаемости (минимальный размер по ТЗ).
        column.setMinimumWidth(280)

        # График слева — масштабируемая область.
        graph.setMinimumSize(500, 400)

        # Разделитель: соотношение график : столбец = 3 : 1 по длине,
        # пользователь тянет за ручку — меняется ширина графика.
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(graph)
        splitter.addWidget(column)
        splitter.setStretchFactor(0, 3)   # график забирает 3 части
        splitter.setStretchFactor(1, 1)   # столбец — 1 часть
        splitter.setSizes([750, 250])
        splitter.setCollapsible(0, False)  # столбец/график не «схлопываются»
        splitter.setCollapsible(1, False)  # в ноль — защита интерфейса
        self.setCentralWidget(splitter)

        self.resize(1024, 640)

    # ------------------------------------------------------------------ #
    # Завершение программы (порядок шагов — в controller.shutdown/main.py) #
    # ------------------------------------------------------------------ #

    def closeEvent(self, event) -> None:
        """Первый closeEvent — начать асинхронное завершение (ignore),
        повторный (после shutdownDone) — принять и закрыться.

        Нельзя закрываться сразу: при включённом output и живом потоке это
        оставит прибор под напряжением и «повисший» процесс (ТЗ).
        """
        if self._shutdown_accepted:
            event.accept()
            return
        event.ignore()
        # Повторные close() до завершения shutdown игнорируем молча: запрос
        # в контроллер уходит один раз, иначе shutdown (и close приборов)
        # выполнился бы дважды.
        if not self._shutdown_requested:
            self._shutdown_requested = True
            self.shutdownRequested.emit()

    def mark_shutdown_done(self) -> None:
        """Вызывается main.py после deviceThread.wait(): разрешает закрытие."""
        self._shutdown_accepted = True
        self.close()
