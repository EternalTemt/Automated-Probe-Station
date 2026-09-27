"""
Точка входа: запуск всего приложения.

Порядок запуска (по разделу ТЗ «Запуск потоков»):
1. QApplication;
2. deviceThread (QThread) + controller/K2636B/PS без родителя + moveToThread;
3. MainWindow и виджеты в GUI-потоке;
4. ВСЕ connect() — в одном месте (здесь, функция wire()), чтобы карта
   связей читалась целиком из одного файла;
5. deviceThread.start();
6. открытие приборов — сигналом controller.startup, который обрабатывается
   уже в worker-потоке (open() до moveToThread запрещён ТЗ: ресурс pyvisa
   привязался бы к GUI-потоку).

Завершение (по разделу ТЗ «Завершение программы»):
- closeEvent окна -> shutdownRequested -> controller.shutdown (worker):
  останов измерения -> output=0 и V=0 на обоих каналах -> K2636B.close()
  и PS.release() -> shutdownDone;
- затем здесь: deviceThread.quit(), deviceThread.wait() (дождаться
  фактического завершения), только после этого закрыть окно и приложение.
"""

import sys

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Slot
from PySide6.QtWidgets import QApplication

from K2636B import K2636B
from PS import PS
from MainWindow import MainWindow
from actual_IV import ActualIV
from controller import Controller
from controller_widget import ControllerWidget
from graph import Graph
from ps_widget import PSWidget

# Явный тип соединения для ВСЕХ межпоточных связей (правило 2 раздела
# «Многопоточность» ТЗ): сигнал кладётся в очередь событий потока-получателя
# и исполняется там. Внутри GUI-потока (межвиджетные связи) — AutoConnection.
QC = Qt.ConnectionType.QueuedConnection


def build_application() -> tuple:
    """Создать приложение, потоки, объекты и окно; НЕ запускать цикл событий.

    Вынесено отдельно от main(), чтобы smoke_check.py собирал ровно ту же
    конфигурацию и дёргал её программно.
    """
    app = QApplication(sys.argv)
    app.setApplicationName("Automated Probe Station")

    # --- 2. Worker-поток и объекты в нём (обязательно без родителей,
    # иначе moveToThread не сработает для объектов с родителем) ---
    deviceThread = QThread()
    deviceThread.setObjectName("deviceThread")   # имя для отладки потоков

    k2636b = K2636B(debug=False)
    ps = PS(debug=False)
    controller = Controller(k2636b, ps)

    # moveToThread ДО создания соединений и ДО открытия приборов.
    for obj in (controller, k2636b, ps):
        obj.moveToThread(deviceThread)

    # --- 3. GUI-поток: виджеты и окно ---
    graph = Graph()
    ps_widget = PSWidget()
    # controller_widget забирает контакты из ps_widget при старте (оба в
    # GUI-потоке — прямой вызов get_contacts() безопасен).
    controller_widget = ControllerWidget(ps_widget=ps_widget)
    actual_iv = ActualIV()
    window = MainWindow(graph, controller_widget, actual_iv, ps_widget)

    # --- 4. Все связи signal/slot — одним списком (см. wire ниже) ---
    wire(controller, k2636b, ps, window, graph,
         controller_widget, actual_iv, ps_widget, deviceThread)

    # Завершение worker-объектов вместе с потоком.
    deviceThread.finished.connect(controller.deleteLater)
    deviceThread.finished.connect(k2636b.deleteLater)
    deviceThread.finished.connect(ps.deleteLater)

    # --- 5. Старт worker-потока ---
    deviceThread.start()

    # --- 6. Открытие приборов уже в worker-потоке ---
    # singleShot(0, ...) отложит эмиссию до входа в цикл событий — после
    # deviceThread.start(), так что слоты initialize() (open/connect)
    # встанут в очередь событий worker-потока, а не GUI.
    QTimer.singleShot(0, controller.startup.emit)

    return app, window, controller, k2636b, ps, deviceThread


def wire(controller, k2636b, ps, window, graph,
         controller_widget, actual_iv, ps_widget, deviceThread) -> None:
    """ВСЕ connect() приложения — в одном месте (требование ТЗ).

    Направления:
      GUI -> контроллер (команды, QueuedConnection в worker);
      контроллер -> GUI (статус и точки, QueuedConnection в GUI);
      драйвер -> GUI (подписка actual_IV на newIV_*, разрешённая ТЗ);
      драйвер -> контроллер (аварии connectionLost/error);
      контроллер -> драйверы (инициализация; прямые вызовы методов
        драйверов из контроллера — не соединения, а его внутренняя логика
        в одном worker-потоке);
      межвиджетные (GUI-поток, AutoConnection);
      завершение программы (shutdownRequested/shutdownDone).
    """
    # ---------------- GUI -> контроллер (команды) ----------------
    controller_widget.sig_start.connect(controller.start, QC)
    controller_widget.sig_pause.connect(controller.pause, QC)
    controller_widget.sig_resume.connect(controller.resume, QC)
    controller_widget.sig_stop.connect(controller.stop, QC)
    # Выбор канала в actual_IV задаёт канал развёртки (по ТЗ — по умолчанию A)
    actual_iv.channelChanged.connect(controller.set_channel, QC)

    # ---------------- Контроллер -> GUI (статус) ----------------
    controller.startDone.connect(controller_widget.on_started, QC)
    controller.startFailed.connect(controller_widget.on_start_failed, QC)
    controller.pauseDone.connect(controller_widget.on_paused, QC)
    controller.resumeDone.connect(controller_widget.on_resumed, QC)
    controller.stopDone.connect(controller_widget.on_stopped, QC)
    # Точки развёртки — на график; новый старт очищает график
    controller.pointUpdated.connect(graph.add_point, QC)
    controller.startDone.connect(graph.clear, QC)
    # Конец измерения: график сохраняет PNG тем же базовым именем, что и
    # данные (путь к JSON -> соседняя папка graphs/, см. graph.py)
    controller.stopDone.connect(graph.on_experiment_finished, QC)

    # ---------------- Драйвер -> GUI (живые значения) ----------------
    # Разрешённое ТЗ исключение: подписка виджета на сигнал-уведомление
    # драйвера — не прямой вызов методов драйвера из GUI.
    k2636b.newIV_A.connect(actual_iv.on_iv_A, QC)
    k2636b.newIV_B.connect(actual_iv.on_iv_B, QC)

    # ---------------- Драйвер -> контроллер (аварии) ----------------
    # Потеря прибора или несоответствие output — контроллер обязан остановить
    # измерение и выключить output (ТЗ).
    k2636b.connectionLost.connect(controller.on_connection_lost, QC)
    k2636b.error.connect(controller.on_device_error, QC)

    # ---------------- Контроллер -> драйверы (worker-поток) ----------------
    # Открытие приборов сигналом инициализации — слоты исполнятся в
    # worker-потоке после его старта (порядок гарантирует build_application).
    controller.startup.connect(controller.initialize, QC)

    # ---------------- Межвиджетные (GUI-поток) ----------------
    controller_widget.chipTypeChanged.connect(ps_widget.set_chip_type)
    controller_widget.scaleChanged.connect(graph.set_log_scale)

    # ---------------- Завершение программы ----------------
    window.shutdownRequested.connect(controller.shutdown, QC)
    # Шаги 4–5 ТЗ: остановить поток, дождаться фактического завершения,
    # только затем закрыть окно (и приложение за ним). Обработчик — слот
    # QObject'а из GUI-потока: без контекста-получателя лямбда-слот исполнился
    # бы в потоке ОТПРАВИТЕЛЯ (worker), и QThread::wait() оказался бы вызван
    # из самого deviceThread (QThread::wait: Thread tried to wait on itself).
    # Родитель + ссылка на окне обязательны: без них координатор — мусор
    # после выхода из wire(), и C++-объект со слотом будет уничтожен.
    coordinator = _ShutdownCoordinator(window, deviceThread, parent=window)
    window._shutdown_coordinator = coordinator
    controller.shutdownDone.connect(coordinator.handle, QC)


class _ShutdownCoordinator(QObject):
    """Получатель shutdownDone: живёт в GUI-потоке и исполняет шаги 4–5
    завершения по ТЗ (quit/wait потока, затем закрытие окна).

    Вынесен в отдельный QObject, а не лямбду, потому что у лямбды-слота без
    контекстного объекта очередная доставка (QueuedConnection) идёт в поток
    ОТПРАВИТЕЛЯ — controller.shutdownDone эмитится из worker-потока, и
    deviceThread.wait() оказался бы вызван из самого deviceThread
    (QThread::wait: Thread tried to wait on itself).
    """

    def __init__(self, window, deviceThread, parent=None):
        super().__init__(parent)
        self._window = window
        self._deviceThread = deviceThread

    @Slot()
    def handle(self) -> None:
        deviceThread = self._deviceThread
        deviceThread.quit()
        if not deviceThread.wait(5000):
            # Поток не завершился за 5 с — аварийная терминация. В норме не
            # случается: worker не должен блокироваться (весь цикл — на таймерах).
            print("# main: deviceThread не завершился штатно — terminate()")
            deviceThread.terminate()
            deviceThread.wait(2000)
        self._window.mark_shutdown_done()


def main() -> int:
    app, window, controller, k2636b, ps, deviceThread = build_application()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
