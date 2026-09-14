# Карта сигналов Automated Probe Station

Архитектура по `Test/shema_asp.jpg`: GUI ↔ контроллер ↔ драйверы приборов,
всё через Qt-сигналы/слоты. Приборы и контроллер живут в одном worker-потоке
(`deviceThread`), GUI — в главном. Связи собираются в `src/ui/main_window.py`.

## Объекты

| Объект              | Файл                      | Поток        | Роль |
|---------------------|---------------------------|--------------|------|
| `MainWindow`        | `src/ui/main_window.py`   | GUI          | поля эксперимента, кнопки, живые u/I, график |
| `SweepController`   | `src/core/controller.py`  | deviceThread | конечный автомат idle/started/paused/ended, цикл развёртки |
| `ContactCheckController` | `src/core/controller.py` | deviceThread | проверка всех 30 контактов: пары (1,1)..(15,15) с подтверждением |
| `K2636B`            | `src/core/devices.py`     | deviceThread | драйвер Keithley 2636B (SMU A/B) |
| `ProbeStation`      | `src/core/devices.py`     | deviceThread | драйвер зондовой станции |

## GUI -> контроллер

| Сигнал              | Слот                    | Смысл |
|---------------------|-------------------------|-------|
| `MainWindow.sig_start(dict)` | `SweepController.start` | начать эксперимент {sample, V1, V2, Vs, dt, contactA, contactB} |
| `MainWindow.sig_pause()`     | `SweepController.pause` | пауза |
| `MainWindow.sig_resume()`    | `SweepController.resume`| продолжить |
| `MainWindow.sig_stop()`      | `SweepController.stop`  | остановить и сохранить |
| `MainWindow.sig_test_start()` | `ContactCheckController.start` | запустить проверку 30 контактов |
| `MainWindow.sig_test_stop()`  | `ContactCheckController.stop`  | прервать тест после текущего шага |

## Контроллер -> GUI

| Сигнал                        | Слот                     | Смысл |
|-------------------------------|--------------------------|-------|
| `SweepController.startDone`   | `MainWindow.on_started`  | эксперимент запущен |
| `SweepController.startFailed(str)` | `MainWindow.on_start_failed` | запуск отклонён (коммутация не подтверждена) |
| `SweepController.stopDone(str)` | `MainWindow.on_stopped` | остановлен, путь к JSON |
| `SweepController.pauseDone/resumeDone` | — | смена состояния кнопок |
| `SweepController.pointUpdated(u, i)` | `MainWindow.on_point` | живые поля u/I |
| `SweepController.dataChanged` | `MainWindow.on_data_changed` | перечитать `e["x"]/e["y"]` под `QReadLocker` |
| `ContactCheckController.testStarted` | `MainWindow.on_test_started` | тест пошёл |
| `ContactCheckController.testProgress(a, b, ok)` | `MainWindow.on_test_progress` | текущая пара — поля A/B в GUI следуют за тестом |
| `ContactCheckController.testDone(aborted, n)` | `MainWindow.on_test_done` | тест завершён/прерван, n пар из 15 |
| `ContactCheckController.testRefused(str)` | `MainWindow.on_hw_error` | тест отклонён (идёт эксперимент) |

## Контроллер -> приборы (тот же поток, прямые вызовы)

* `probe.setContacts(a, b)` -> `bool` — при старте эксперимента: замкнуть пару
  контактов (a — сторона A, b — сторона B, каждый 1..15). Транзакция с
  подтверждением: команда МК → ожидание ACK (`ACK_TIMEOUT = 1.0 с`) →
  выдержка на переключение реле (`SETTLE_TIME = 0.1 с`). Контроллер не
  подаёт напряжение, пока `setContacts` не вернул `True` — иначе
  `startFailed` и эксперимент не стартует;
* `k2636b.setVoltage(u)` — каждая точка развёртки;
* `k2636b.measure(u)` -> `i` — измерение (также эмитит `newPoint`).

## Приборы -> GUI (опционально, для подтверждений)

* `K2636B.newVoltage(u)` — подтверждение установки напряжения (зелёная подсветка поля);
* `K2636B.newPoint(u, i)` — измеренная точка;
* `K2636B.connectionLost` — прибор отвалился;
* `ProbeStation.contactsChanged(a, b)` — МК подтвердил коммутацию (зелёная подсветка полей A/B);
* `ProbeStation.released` — все реле разомкнуты;
* `ProbeStation.error(str)` — контакт вне 1..15 или сбой связи с МК.

## Механика цикла (как в reference/dsr)

`next_point()` обрабатывает одну точку и перепланирует себя через
`sig_next_point` с `Qt.QueuedConnection` — цикл крутится в очереди событий
worker-потока, поэтому слоты pause/stop успевают обрабатываться, а GUI не
блокируется. Общие данные `controller.e` защищены `QReadWriteLock`.
