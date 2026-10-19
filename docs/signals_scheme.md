# Карта сигналов Automated Probe Station (новый каркас, demo/)

Актуальная версия для каркаса в `demo/`.
Все соединения собираются в ОДНОМ месте — `demo/main.py`, функция `wire()`. При изменении
сигнатур сигналов/слотов этот файл обновлять в первую очередь.

## Потоки и объекты

| Поток | Объекты |
|---|---|
| GUI-поток (главный) | `QApplication`, `MainWindow`, `Graph`, `ControllerWidget`, `ActualIV`, `PSWidget`, `History`, `Session` |
| worker-поток `deviceThread` (`QThread`) | `Controller`, `K2636B`, `PS` |

Правила обмена (раздел «Многопоточность» ТЗ):

- между потоками — только сигналы/слоты, все межпоточные connect — с
  `Qt.ConnectionType.QueuedConnection` (константа `QC` в `main.py`);
- контроллер внутри worker-потока вызывает методы `K2636B` и `PS` **напрямую**
  (одна очередь событий, гонки нет) — в таблицах ниже это «прямые вызовы»;
- GUI драйверы напрямую не вызывает; исключение — подписка `ActualIV` на
  сигналы-уведомления `K2636B.newIV_*` (подписка на сигнал — не вызов);
- данные между потоками — копии простых типов в аргументах сигналов.

Запуск: `build_application()` в `demo/main.py` — порядок строго по ТЗ
(QApplication → объекты без родителей → moveToThread → виджеты → wire() →
`deviceThread.start()` → открытие приборов сигналом `controller.startup`
уже в worker-потоке через `QTimer.singleShot(0, ...)`).

Завершение: `MainWindow.closeEvent` (первый раз ignore + `shutdownRequested`,
повторный — accept) → `Controller.shutdown` в worker-потоке →
`shutdownDone` → `MainWindow.on_shutdown_done` в GUI-потоке →
`deviceThread.quit()` + `wait()` → повторный `close()` с accept.
Слот окна исполняется в GUI-потоке, потому что MainWindow там живёт, —
поэтому `QThread::wait()` не вызывается из самого deviceThread и отдельный
QObject-координатор не нужен.

## Конечный автомат измерения (Controller)

Три глобальных состояния: `idle` (измерения не было — задание параметров),
`measurements` (основной процесс — работает железо, снимается ВАХ),
`waiting` (режим ожидания — цикл остановлен, просмотр промежуточных
результатов). Константы `IDLE/MEASUREMENTS/WAITING` в `demo/controller.py`.
Все действия — **на переходах**, состояние — только охрана переходов.

| Переход | Инициатор | Действия контроллера на переходе |
|---|---|---|
| idle → measurements | `ControllerWidget.sig_start` | валидация параметров → `PS.set_contacts(a, b)` (только `True` пропускает, иначе `startFailed`) → `K2636B.set_i_max_*` → `set_output_*=1` (с перепроверкой `get_output_*`) → построение плана 0→V2→V1→0 → `startDone` → цикл точек |
| measurements → waiting | `sig_pause` | отмена запланированных точек (поколение `_token`), `set_output_*=0` → `pauseDone` |
| waiting → measurements | `sig_resume` | `set_output_*=1`, возврат напряжения точки `_idx-1`, выдержка delay (`QTimer.singleShot`), продолжение цикла → `resumeDone` |
| measurements/waiting → idle | `sig_stop` (кнопка stop) **или** естественный конец плана | `set_output_*=0`, V=0, сохранение .vac, `stopDone(vac_path)`; при естественном конце дополнительно `finished` |
| measurements → idle (авария i_max) | измеренный `|I| >= i_max` (проверка в `_measure_point`) | те же действия, что по stop (output=0, V=0, сохранение данных) + `startFailed(причина)` — GUI показывает «Экстренная остановка: \|I\| достиг i_max» |
| measurements/waiting → idle (авария прибора) | `K2636B.connectionLost` / `K2636B.error` | попытка `output=0`, `startFailed(причина)` |

Цикл развёртки событийный: одна точка = `set_voltage` → выдержка delay через
`QTimer.singleShot` в worker-потоке → `get_iv` → `pointUpdated(u, i)` →
внутренний сигнал `sig_next_point` (QueuedConnection) → слот `next_point`.
`while`/`time.sleep` не используются — pause/stop успевают между точками.
Семантика delay (ТЗ): задержка стартует сразу после `get_iv` предыдущей
точки и отсрочивает следующее измерение, покрывая время установления нового
напряжения (`set_voltage` выполняется в её начале); время установления
неизвестно, поэтому delay задаётся пользователем с запасом.
Отмена отложенных вызовов — через счётчик поколений `Controller._token`
(pause/stop/завершение инкрементируют; замыкание таймера срабатывает
только при совпадении поколения).

## GUI → контроллер (команды, QueuedConnection)

| Сигнал | Слот | Аргументы / смысл |
|---|---|---|
| `ControllerWidget.sig_start` | `Controller.start` | `dict`: sample, V1, V2, Vs, dt, chipType, i_max, scale, contactA, contactB, session_dir (папка образца, создаётся при start), limits |
| `ControllerWidget.sig_pause` | `Controller.pause` | пауза (output=0) |
| `ControllerWidget.sig_resume` | `Controller.resume` | продолжение (output=1, возврат V, выдержка delay) |
| `ControllerWidget.sig_stop` | `Controller.stop` | останов + сохранение (кнопка stop) |
| `ActualIV.channelChanged` | `Controller.set_channel` | `str` "A"/"B" — канал развёртки (в idle) |
| `MainWindow.shutdownRequested` | `Controller.shutdown` | завершение программы (см. порядок выше) |
| (внутренний) `Controller.sig_next_point` | `Controller.next_point` | самоперепланировка цикла (QueuedConnection) |

Доступность кнопок (ControllerWidget): в idle `start` неактивен, пока поле
«Образец» пустое; `stop` неактивен в idle и активен в measurements и
waiting (досрочное завершение, в т.ч. с паузы).

## Контроллер → GUI (статус и данные, QueuedConnection)

| Сигнал | Слот(ы) | Смысл |
|---|---|---|
| `Controller.startDone` | `ControllerWidget.on_started`, `Graph.clear`, `ActualIV.lock` | измерение запущено: блокировать поля и выбор канала, кнопка → «pause», график чистится, watcher session — на local_config образца |
| `Controller.startFailed(str)` | `ControllerWidget.on_start_failed`, `ActualIV.unlock` | старт отклонён (валидация/коммутация/output) или вынужденный переход в idle (авария i_max, потеря связи, ошибка прибора): причина в status_label, остаться в idle |
| `Controller.pauseDone` | `ControllerWidget.on_paused` | кнопка → «resume» |
| `Controller.resumeDone` | `ControllerWidget.on_resumed` | кнопка → «pause» |
| `Controller.stopDone(str)` | `ControllerWidget.on_stopped`, `History.on_measurement_saved`, `Graph.clear`, `ActualIV.unlock` | остановлено; str — путь к .vac ("" при ошибке сохранения). Живая кривая очищается; файл появляется первым в блоке истории **с прожатой галочкой** — измерение сразу показывается из файла; выбор канала разблокируется |
| `Controller.pointUpdated(u, i)` | `Graph.add_point` | точка развёртки на график I(U) |
| `Controller.progressUpdated(int)` | `ControllerWidget.progress_bar.setValue` | процент выполнения развёртки (измерено точек / всего в плане — при постоянном темпе Δt это доля затраченного времени); 0 на старте и при stop, 100 при естественном конце |
| `Controller.finished` | (подписка опциональна, слушает smoke_check) | естественный конец развёртки (после `stopDone`) |
| `Controller.shutdownDone` | `MainWindow.on_shutdown_done` | приборы закрыты — можно гасить поток и окно |

## Межвиджетные связи (GUI-поток, AutoConnection)

| Сигнал | Слот | Смысл |
|---|---|---|
| `ControllerWidget.chipTypeChanged(str)` | `PSWidget.set_chip_type` | тип чипа OP2/MD2 → диапазон контактов 1..13 / 1..15, ячейки доступны |
| `ControllerWidget.scaleChanged(bool)` | `Graph.set_log_scale` | True = логарифмическая шкала тока (вид оси, `setLogMode`; не пересчёт значений) |
| `MainWindow.themeChanged(str)` | apply_theme (замыкание в `wire()`) | применить тему: `app.setStyleSheet(theme.qss(name))` + `graph.apply_theme(theme.graph_colors(name))` |
| `History.curveToggled(str, bool, QColor)` | on_curve_toggled (замыкание в `wire()`) | галочка файла: прочитать .vac и `Graph.add_history_curve(path, u, i, color)` / снять — `Graph.remove_history_curve(path)`; битый файл → `History.mark_error(path)` |
| `History.primaryChanged(str)` | on_primary_changed (замыкание в `wire()`) | «Сделать основной»: `Session.set_active_config` на local_config.json этой папки (если есть) |
| `History.workdirChanged(str)` | on_workdir_changed (замыкание в `wire()`) | «Рабочая папка…»: `Session.set_workdir(dir)` + запомнить в QSettings |
| `Session.configChanged(dict)` | `ControllerWidget.apply_config_safe` | активный конфиг изменён на диске (QFileSystemWatcher): применить к полям в idle, во время измерения — отложить до stop |
| `ActualIV.channelChanged(str)` | см. также таблицу GUI → контроллер | виджет сам переключает отображаемый канал |

Прямые вызовы внутри GUI-потока (не сигналы, разрешено — запрет касается
только межпоточных вызовов): `ControllerWidget._collect_params()` →
`PSWidget.get_contacts()` и `Session.sample_dir(sample)` при сборке dict
старта (папка образца создаётся здесь); `apply_config()` →
`PSWidget.set_contacts(a, b)`; смена имени образца →
`Session.set_active_config(local_config, если есть)`. При старте
`build_application()` вызывает `ControllerWidget._init_from_config()`
(поля и лимиты из активного конфига в GUI).

## Контроллер → драйверы (worker-поток, прямые вызовы)

Диспетчеризация по `Controller.channel` ("A"/"B"):

| Вызов | Драйвер | Смысл |
|---|---|---|
| `set_contacts(a, b) → bool` | `PS` | коммутация до подачи напряжения; `False`/`error` → `startFailed` |
| `release()` | `PS` | разомкнуть все реле (shutdown) |
| `set_i_max_A/B(limit)` | `K2636B` | `smuX.source.limiti` ДО включения output |
| `set_output_A/B(bool)` | `K2636B` | главный рубильник; только на время измерения |
| `get_output_A/B() → bool` | `K2636B` | фактический статус (перепроверка после установки) |
| `set_voltage_A/B(v)` | `K2636B` | уровень развёртки |
| `get_iv_A/B() → (i, v)` | `K2636B` | измерение точки |
| `open()` / `close()` | `K2636B` | по сигналу `startup` / из `shutdown` |
| `connect()` / `close()` | `PS` | аналогично |

## Драйверы → подписчики (уведомления)

| Сигнал | Получатель | Тип соединения | Смысл |
|---|---|---|---|
| `K2636B.newIV_A / newIV_B(tuple)` | `ActualIV.on_iv_A / on_iv_B` | QueuedConnection | живая пара (i, v) — виджет показывает выбранный канал |
| `K2636B.newOut_A / newOut_B(bool)` | (пока без подписчиков; слушает контроллер возвратом `get_output_*`) | — | фактический статус output |
| `K2636B.connectionLost` | `Controller.on_connection_lost` | QueuedConnection | прибор не отвечает → аварийная остановка |
| `K2636B.error(str)` | `Controller.on_device_error` | QueuedConnection | расхождение статуса output и пр. → аварийная остановка |
| `PS.contactsChanged(a, b)` | (резерв под будущий GUI; контроллер проверяет возврат `True`) | — | подтверждённая коммутация |
| `PS.released` | (резерв) | — | все реле разомкнуты |
| `PS.error(str)` | (резерв; контроллер ловит `False` от `set_contacts`) | — | ошибка станции |

## Сохранение данных (только Controller)

- Рабочая папка запуска (ТЗ «Данные и сохранение»): аргумент `-d`
  (`make run d=<имя>` — подпапка `demo/data/<имя>/`, абсолютный путь — как
  есть) → последняя выбранная в GUI (QSettings) → `demo/data/`
  (`DEFAULT_WORKDIR` в session.py, в `.gitignore`).
- В рабочей папке — `config.json` (скелет параметров) и папки образцов
  `<workdir>/<sample>/`, создаваемые при нажатии start. В папке образца —
  `local_config.json` (копия config.json при первом старте этого образца)
  и результаты измерений. Поля `sample` в конфигах нет.
- Результат измерения — файл `.vac` (JSON внутри) в папке образца; имя
  `YYYY-MM-DD_HH-MM-SS_<образец>_A<NN>_B<NN>.vac` (контакт — две цифры с
  ведущим нулём: `A03_B04`). При коллизии в пределах одной секунды —
  суффикс `_1`, `_2`, …
- Содержимое .vac: `params` (sample, chipType, V1, V2, Vs, dt, i_max,
  channel, contactA, contactB), массивы `u`, `i`, `saved_at`. Ключ
  `i_max` — с замены термина compliance; файлы, сохранённые до переименования,
  по этому ключу несовместимы. (Расширение `.vag` упразднено в пользу `.vac`.)
- CSV и PNG не создаются (упразднены).

## Проверка

`demo/smoke_check.py` — программный headless-прогон (122 проверки) во
временной рабочей папке (`tempfile`, `demo/data/` не засоряется):
инициализация → config.json в рабочей папке, поля/лимиты GUI из конфига,
папки образцов до start не создаются, start/stop погашены в idle →
watcher: правка config.json из консоли подтягивается в GUI → шаг стрелок
V1/V2 следует за Vs → межвиджетные связи (масштаб, канал) → тема/шрифт/рамки
→ старт (TEST1, MD2, ±1 В, Vs=0.2, Δt=0.08, i_max=0.07): папка образца и
local_config.json созданы → точки (stop активен и в measurements, и на
паузе; правка local_config во время измерения откладывается) → pause →
resume → stop → .vac по шаблону имени с датой, живая кривая очищена,
отложенный конфиг применён → история (файл с авто-галочкой, кривая из
файла, ленивое раскрытие папок, поиск-фильтр, «Сделать основной»,
«Удалить из Истории» без удаления с диска, «Открыть в проводнике»
(вызов с подменой, без реального окна), битый .vac помечается
«(ошибка)», «Удалить с устройства» снимает кривую) → смена рабочей папки
(session переключается, config.json создаётся, дерево перестраивается) →
ручной stop посреди развёртки (TEST2, finished не эмитится, темп delay
соблюдён) → естественный конец (TEST3, finished, idle, прогресс 100%) →
авария i_max (TEST4, i_max=1 нА: экстренная остановка, данные сохранены,
причина в GUI) → повторный start с тем же образцом (файлы добавляются в
ту же папку) → shutdown по ТЗ. Запуск:

```bash
QT_QPA_PLATFORM=offscreen .asp_env/bin/python demo/smoke_check.py
```
