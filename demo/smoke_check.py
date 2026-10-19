import json
import os
import re
import sys
import tempfile
import time
import traceback

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import build_application  # noqa: E402
from controller_widget import ControllerWidget  # noqa: E402
from graph_widget import Graph  # noqa: E402
from K2636B_widget import ActualIV  # noqa: E402
from history_widget import History  # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, what: str) -> None:
    """Условие проверки: при ложе — raise, прерываем сценарий с диагностикой."""
    if cond:
        print(f"  ok: {what}")
    else:
        raise AssertionError(what)


def list_files(dir_path: str) -> set:
    return set(os.listdir(dir_path)) if os.path.isdir(dir_path) else set()


class Smoke:
    """Шаговый исполнитель сценария: (задержка_мс, функция) по очереди."""

    def __init__(self, app, window, controller, k2636b, ps, workdir):
        self.app = app
        self.window = window
        self.c = controller
        self.k = k2636b
        self.ps = ps
        self.workdir = workdir
        self.cw = window.findChild(ControllerWidget)
        self.graph = window.findChild(Graph)
        assert self.cw is not None and self.graph is not None
        self._steps: list = []

    def then(self, delay_ms: int, fn) -> "Smoke":
        self._steps.append((delay_ms, fn))
        return self

    def run(self, after) -> None:
        """Запустить цепочку; after(exit_code) — в конце (успех/первая ошибка)."""
        self._after = after
        self._next()

    def _next(self) -> None:
        if not self._steps:
            print("SMOKE: все проверки пройдены")
            self._after(0)
            return
        delay, fn = self._steps.pop(0)

        def invoke():
            try:
                fn(self)
            except Exception:
                FAILURES.append(traceback.format_exc())
                print("SMOKE FAIL:\n" + FAILURES[-1])
                self._after(1)
                return
            self._next()

        QTimer.singleShot(delay, invoke)


def _wait_for(cond, what: str, timeout_s: float = 3.0) -> None:
    """Ждать истинности условия с таймаутом."""
    for _ in range(int(timeout_s * 20)):
        if cond():
            print(f"  ok: {what}")
            return
        time.sleep(0.05)
    raise AssertionError(what)


def _rewrite_config(path: str, **value_updates) -> None:
    """Перезаписать values конфига на диске (правка «из консоли»)."""
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["values"].update(value_updates)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def step_wait_init(s: Smoke) -> None:
    """Дождаться открытия приборов в worker-потоке (таймаут 5 с)."""
    for _ in range(50):
        if s.k.opened and s.ps.connected:
            break
        time.sleep(0.1)
        s.app.processEvents()
    check(s.k.opened, "K2636B открыт")
    check(s.ps.connected, "PS подключена")
    print(f"  info: K2636B.simulated = {s.k.simulated} (ожидаем True без железа)")
    check(s.k.simulated, "K2636B в режиме симуляции")
    check(s.c.state == "idle", "контроллер в idle после инициализации")
    check(os.path.isfile(os.path.join(s.workdir, "config.json")),
          "config.json создан в рабочей папке при старте")
    check(list_files(s.workdir) == {"config.json"},
          "папки образцов до первого start не создаются")


def step_config_startup(s: Smoke) -> None:
    """Поля и лимиты GUI при старте соответствуют config.json; кнопки погашены."""
    cw = s.cw
    with open(os.path.join(s.workdir, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    check("sample" not in cfg["values"], "config.json: поля sample нет")
    check(cw.v1_spin.value() == cfg["values"]["V1"], "старт: V1 из config.json")
    check(cw.v1_spin.minimum() == cfg["limits"]["V_min"],
          "старт: лимит V_min в диапазоне спинбокса")
    check(cw.i_max_spin.maximum() == cfg["limits"]["i_max_limit"],
          "старт: лимит i_max в диапазоне спинбокса")
    check(not cw.sample_edit.text(), "старт: поле образца пустое")
    check(not cw.start_button.isEnabled(), "старт: start неактивен без имени образца")
    check(not cw.stop_button.isEnabled(), "старт: stop неактивен в idle")


def step_watcher_idle_write(s: Smoke) -> None:
    """Правка config.json «из консоли» (watcher подтянет в следующем шаге)."""
    _rewrite_config(os.path.join(s.workdir, "config.json"), V1=-0.33)


def step_watcher_idle_check(s: Smoke) -> None:
    check(s.cw.v1_spin.value() == -0.33,
          "watcher: правка config.json автоматически подтянулась в GUI (idle)")


def step_set_params_full(s: Smoke) -> None:
    cw = s.cw
    cw.sample_edit.setText("TEST1")
    check(cw.start_button.isEnabled(), "start активен после ввода имени образца")
    cw.radio_md2.click()
    cw.v1_spin.setValue(-1.0)
    cw.v2_spin.setValue(1.0)
    cw.vs_spin.setValue(0.2)
    check(cw.v1_spin.singleStep() == 0.2 and cw.v2_spin.singleStep() == 0.2,
          "шаг стрелок V1/V2 следует за Vs (кратность шагу развёртки)")
    cw.dt_spin.setValue(0.08)
    cw.i_max_spin.setValue(0.07)
    check(cw._ps_widget.combo_a.isEnabled(), "ячейки контактов доступны после выбора типа чипа")
    check(cw._ps_widget.combo_a.count() == 15, "диапазон MD2: 1..15")
    cw._ps_widget.combo_a.setCurrentText("3")
    cw._ps_widget.combo_b.setCurrentText("4")


def step_exercise_scale_and_channel(s: Smoke) -> None:
    """Проверить связи scaleChanged и channelChanged между виджетами."""
    s.cw.radio_log.click()
    check(s.graph._log_scale, "scaleChanged -> set_log_scale(True)")
    check(s.graph.plot_widget.getPlotItem().getViewBox().state["logMode"] == [False, True],
          "лог-режим — через setLogMode оси (вид), не пересчёт значений")
    s.cw.radio_lin.click()
    check(not s.graph._log_scale, "scaleChanged -> set_log_scale(False)")

    s.aiv = s.window.findChild(ActualIV)
    aiv = s.aiv
    aiv.radio_b.click()
    _wait_for(lambda: s.c.channel == "B", "channelChanged -> set_channel('B')")
    check(aiv.current_channel() == "B", "actual_IV показывает канал B")
    aiv.radio_a.click()
    _wait_for(lambda: s.c.channel == "A", "channelChanged -> set_channel('A')")


def step_exercise_theme(s: Smoke) -> None:
    """Проверить шрифт, рамки виджетов и переключение светлой/тёмной темы."""
    from PySide6.QtWidgets import QGroupBox
    check(s.app.font().pointSize() >= 12, "базовый шрифт приложения >= 12 pt")
    check(isinstance(s.cw.parentWidget(), QGroupBox), "controller_widget в рамке QGroupBox")
    check(isinstance(s.graph.parentWidget(), QGroupBox), "graph в рамке QGroupBox")
    check(s.window.theme_combo.isEnabled(), "селектор темы доступен")
    s.window.theme_combo.setCurrentText("Тёмная")
    check("#2b2b2b" in s.app.styleSheet(), "тёмная тема применена (QSS)")
    s.window.theme_combo.setCurrentText("Светлая")
    check("#f5f5f5" in s.app.styleSheet(), "светлая тема применена (QSS)")


def step_start(s: Smoke) -> None:
    s._n0 = s.graph.points_count()
    s.cw.start_button.click()


def step_points_flow(s: Smoke) -> None:
    check(s.c.state == "measurements", "после start контроллер в measurements")
    check(s.graph.points_count() > s._n0, "точки идут на график")
    check(s.cw.start_button.text() == "pause", "кнопка стала «pause»")
    check(s.cw.start_button.isEnabled(), "start/pause активен во время измерения")
    check(s.cw.stop_button.isEnabled(), "stop активен во время измерения")
    check(not s.cw.v1_spin.isEnabled(), "поля заблокированы при измерении")
    check(s.k.outputA, "output канала A включён на время измерения")
    sample_dir = os.path.join(s.workdir, "TEST1")
    check(os.path.isdir(sample_dir), "папка образца создана при нажатии start")
    check(os.path.isfile(os.path.join(sample_dir, "local_config.json")),
          "local_config.json создан в папке образца при первом start")
    s.cw.radio_log.click()
    check(s.graph._log_scale, "переключение в лог во время измерения")
    s.cw.radio_lin.click()
    check(not s.aiv.radio_a.isEnabled(), "выбор канала A/B заблокирован во время измерения")
    check(s.cw.progress_bar.value() > 0, "прогресс-бар идёт во время измерения")
    check(s.window.theme_combo.isEnabled(), "селектор темы доступен во время измерения")
    _rewrite_config(os.path.join(sample_dir, "local_config.json"), V1=-0.7)


def step_pause(s: Smoke) -> None:
    s.cw.start_button.click()


def step_paused(s: Smoke) -> None:
    check(s.c.state == "waiting", "после pause контроллер в waiting")
    check(s.cw.start_button.text() == "resume", "кнопка стала «resume»")
    check(s.cw.stop_button.isEnabled(), "stop активен на паузе (досрочное завершение)")
    check(not s.k.outputA, "на паузе output=0 (напряжение снято с образца)")
    s._paused_count = s.graph.points_count()


def step_paused_frozen(s: Smoke) -> None:
    check(s.graph.points_count() == s._paused_count,
          "на паузе точки не накапливаются")


def step_resume(s: Smoke) -> None:
    s.cw.start_button.click()
    s._resumed_count = s.graph.points_count()


def step_resumed(s: Smoke) -> None:
    check(s.graph.points_count() > s._resumed_count,
          "после resume (с выдержкой delay) точки продолжились")
    check(s.c.state in ("measurements", "idle"),
          f"после resume состояние корректно (measurements/idle), а не {s.c.state}")


def step_stop(s: Smoke) -> None:
    s.cw.stop_button.click()


def step_after_stop(s: Smoke) -> None:
    check(s.c.state == "idle", "после stop контроллер вернулся в idle")
    check(s.cw.start_button.text() == "start", "кнопка вернулась в «start»")
    check(not s.cw.stop_button.isEnabled(), "stop неактивен после остановки (idle)")
    check(s.cw.v1_spin.isEnabled(), "поля разблокированы после остановки")
    check(not s.k.outputA, "после stop output=0")
    check(s.aiv.radio_a.isEnabled(), "выбор канала разблокирован после остановки")
    check(s.cw.v1_spin.value() == -0.7,
          "watcher: правка local_config во время измерения применена после stop")

    sample_dir = os.path.join(s.workdir, "TEST1")
    vacs = [f for f in list_files(sample_dir) if f.endswith(".vac")]
    check(len(vacs) == 1, f"появился ровно один .vac: {vacs}")
    only = vacs[0]
    s._vac_path = os.path.join(sample_dir, only)

    check(re.match(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_TEST1_A03_B04(_\d+)?\.vac$",
                   only) is not None,
          f"имя .vac по шаблону с датой: {only}")

    with open(s._vac_path, encoding="utf-8") as f:
        d = json.load(f)
    p = d["params"]
    for key in ("sample", "chipType", "V1", "V2", "Vs", "dt", "i_max",
                "channel", "contactA", "contactB"):
        check(key in p, f".vac params содержит {key}")
    check(p["sample"] == "TEST1" and p["chipType"] == "MD2", ".vac: sample/chipType")
    check(p["contactA"] == 3 and p["contactB"] == 4, ".vac: контакты в параметрах")
    check(p["channel"] == "A", ".vac: канал развёртки A")
    check(p["V1"] == -1.0 and p["V2"] == 1.0 and p["Vs"] == 0.2 and p["dt"] == 0.08,
          ".vac: параметры развёртки (с момента start, не watcher)")
    check(len(d["u"]) > 0 and len(d["i"]) == len(d["u"]), ".vac: массивы точек")

    check(s.graph.points_count() == 0, "после stop живая кривая очищена")


def step_exercise_history(s: Smoke) -> None:
    """История: авто-галочка, ленивое раскрытие, поиск, основная папка, удаление."""
    h = s.window.findChild(History)
    check(h is not None, "виджет истории присутствует в окне")
    s.history = h

    check(s._vac_path in h._items, "история: свежий .vac появился в списке после stop")
    item = h._items[s._vac_path]
    block = item.parent()
    check(item.checkState(0) == Qt.CheckState.Checked,
          "история: галочка свежего файла прожата автоматически")
    check(h.tree.topLevelItem(0) is block, "история: блок свежего измерения наверху")
    check(s.graph.history_curve_count() == 1,
          "история: кривая свежего файла показана на графике (из файла)")
    check(not item.icon(0).isNull(), "история: цветной квадрат у отмеченного файла")
    item.setCheckState(0, Qt.CheckState.Unchecked)
    check(s.graph.history_curve_count() == 0, "история: снятие галочки убрало кривую")
    item.setCheckState(0, Qt.CheckState.Checked)
    check(s.graph.history_curve_count() == 1, "история: повторная галочка вернула кривую")

    alpha = os.path.join(s.workdir, "ALPHA_DD05")
    os.makedirs(alpha)
    with open(os.path.join(alpha, "2026-01-01_00-00-00_asrv_DD05_A01_B02.vac"),
              "w", encoding="utf-8") as f:
        json.dump({"params": {}, "u": [0.0, 1.0], "i": [0.0, 0.1]}, f)
    with open(os.path.join(alpha, "local_config.json"), "w", encoding="utf-8") as f:
        json.dump({"values": {"V1": -0.55}}, f)
    alpha_block = h._add_block(alpha)
    check(alpha_block.childCount() == 1
          and alpha_block.child(0).text(0) == "…",
          "история: папка добавлена без сканирования вглубь (ленивый блок)")
    alpha_block.setExpanded(True)
    check(any(alpha_block.child(j).text(0).endswith(".vac")
              for j in range(alpha_block.childCount())),
          "история: раскрытие папки сканирует её (веточное ветвление)")

    h.search_edit.setText("dd05")
    check(not alpha_block.isHidden(), "поиск: папка по подстроке имени видна")
    check(block.isHidden(), "поиск: неподходящая папка скрыта")
    h.search_edit.setText("")
    check(not block.isHidden(), "поиск: очистка строки вернула все папки")

    h._move_block_to_top(alpha)
    check(h.tree.topLevelItem(0) is alpha_block,
          "«Сделать основной»: папка поднята наверх")
    h.primaryChanged.emit(alpha)
    from session import Session
    sess = s.cw._session
    check(sess.active_config == os.path.join(alpha, "local_config.json"),
          "«Сделать основной»: watcher переключён на local_config этой папки")
    h._forget_block(alpha_block)
    check(h.tree.indexOfTopLevelItem(alpha_block) == -1,
          "«Удалить из Истории»: папка убрана из дерева")
    check(os.path.isdir(alpha), "«Удалить из Истории»: папка осталась на диске")

    opened = []
    orig_exec = History._exec_menu
    orig_open = History._open_in_explorer
    History._exec_menu = lambda self, menu, pos: menu.actions()[2]
    History._open_in_explorer = lambda self, p: opened.append(p)
    try:
        test1_block = h._blocks[os.path.join(s.workdir, "TEST1")]
        h._on_context_menu(h.tree.visualItemRect(test1_block).center())
    finally:
        History._exec_menu = orig_exec
        History._open_in_explorer = orig_open
    check(opened == [os.path.join(s.workdir, "TEST1")],
          "контекстное меню папки: «Открыть в проводнике» открывает её каталог")

    broken = os.path.join(s.workdir, "TEST1", "00-00-00_BROKEN_A01_B01.vac")
    with open(broken, "w", encoding="utf-8") as f:
        f.write("это не json")
    h.add_file(broken)
    broken_item = h._items[broken]
    broken_item.setCheckState(0, Qt.CheckState.Checked)
    check(s.graph.history_curve_count() == 1, "история: битый .vac не дал кривую")
    check("(ошибка)" in broken_item.text(0), "история: битый .vac помечен «(ошибка)»")

    h._delete([s._vac_path, broken])
    check(not os.path.exists(s._vac_path), "история: файл удалён с диска")
    check(s._vac_path not in h._items, "история: файл удалён из списка")
    check(s.graph.history_curve_count() == 0, "история: кривая удалённого файла снята")


def step_workdir_switch(s: Smoke) -> None:
    """Смена рабочей папки из GUI: дерево и session перестраиваются."""
    h = s.history
    sess = s.cw._session
    other = tempfile.mkdtemp(prefix="aps_smoke_other_")
    h.set_workdir(other)
    h.workdirChanged.emit(other)
    check(sess.workdir == os.path.abspath(other), "рабочая папка: session переключена")
    check(os.path.isfile(os.path.join(other, "config.json")),
          "рабочая папка: config.json создан в новой папке")
    check(h.tree.topLevelItemCount() == 0, "рабочая папка: дерево пустой папки пусто")
    h.set_workdir(s.workdir)
    h.workdirChanged.emit(s.workdir)
    check(sess.workdir == os.path.abspath(s.workdir),
          "рабочая папка: возврат в исходную")
    check(h.tree.topLevelItemCount() >= 1,
          "рабочая папка: дерево перестроено (папка TEST1 снова в списке)")


def step_set_params_mid(s: Smoke) -> None:
    """Настроить короткую медленную развёртку для проверки ручного stop."""
    cw = s.cw
    cw.sample_edit.setText("TEST2")
    cw.v1_spin.setValue(-0.2)
    cw.v2_spin.setValue(0.2)
    cw.vs_spin.setValue(0.2)
    cw.dt_spin.setValue(0.05)
    s._finished_seen = False
    s.c.finished.connect(lambda: setattr(s, "_finished_seen", True))
    s._times = []
    s.c.pointUpdated.connect(lambda u, i: s._times.append(time.monotonic()))


def step_start_mid(s: Smoke) -> None:
    s.cw.start_button.click()


def step_stop_mid(s: Smoke) -> None:
    """Останавливаем посреди развёртки, пока точки ещё идут."""
    check(s.c.state == "measurements", "сегмент 2: развёртка идёт перед ручным stop")
    check(s.graph.points_count() > 0, "сегмент 2: есть точки перед ручным stop")
    s.cw.stop_button.click()


def step_after_stop_mid(s: Smoke) -> None:
    check(s.c.state == "idle", "после ручного stop — idle")
    check(not s._finished_seen, "при ручном stop finished НЕ эмитится")
    check(not s.k.outputA, "после ручного stop output=0")
    if len(s._times) >= 2:
        intervals = [b - a for a, b in zip(s._times, s._times[1:])]
        check(min(intervals) >= 0.9 * 0.05,
              f"темп delay соблюдён: интервалы {[round(x, 3) for x in intervals]} >= 0.045 с")
    vacs = [f for f in list_files(os.path.join(s.workdir, "TEST2")) if f.endswith(".vac")]
    check(len(vacs) == 1, f"ручной stop сохранил данные в папку TEST2: {vacs}")
    with open(os.path.join(s.workdir, "TEST2", vacs[0]), encoding="utf-8") as f:
        d = json.load(f)
    check(d["params"]["sample"] == "TEST2", ".vac сегмента 2: sample=TEST2")


def step_set_params_short(s: Smoke) -> None:
    cw = s.cw
    cw.sample_edit.setText("TEST3")
    cw.v1_spin.setValue(-0.2)
    cw.v2_spin.setValue(0.2)
    cw.vs_spin.setValue(0.2)
    cw.dt_spin.setValue(0.005)
    s._finished_seen = False
    s.c.finished.connect(lambda: setattr(s, "_finished_seen", True))


def step_start_short(s: Smoke) -> None:
    s.cw.start_button.click()


def step_wait_natural_end(s: Smoke) -> None:
    """Поллинг до естественного конца развёртки — флага finished (таймаут 15 с).

    Ориентир — именно сигнал finished: state==idle истинно и ДО обработки
    sig_start worker-потоком, такой поллинг завершается раньше времени."""
    for _ in range(150):
        if s._finished_seen:
            break
        time.sleep(0.1)
        s.app.processEvents()
    check(s._finished_seen, "сигнал finished эмитнут при естественном конце")
    check(s.c.state == "idle", "после естественного конца развёртки — idle")
    check(not s.k.outputA, "после естественного конца output=0")
    check(s.cw.progress_bar.value() == 100, "прогресс 100% после естественного конца")
    vacs = [f for f in list_files(os.path.join(s.workdir, "TEST3")) if f.endswith(".vac")]
    check(len(vacs) == 1, f"третий эксперимент сохранён в папку TEST3: {vacs}")


def step_set_params_emergency(s: Smoke) -> None:
    """Развёртка с i_max=1 нА: симуляция упирается в ограничение на 2-й точке."""
    cw = s.cw
    cw.sample_edit.setText("TEST4")
    cw.v1_spin.setValue(-0.2)
    cw.v2_spin.setValue(0.2)
    cw.vs_spin.setValue(0.2)
    cw.dt_spin.setValue(0.005)
    cw.i_max_spin.setValue(1e-9)


def step_start_emergency(s: Smoke) -> None:
    s.cw.start_button.click()


def step_after_emergency(s: Smoke) -> None:
    """Ждать аварийной остановки: причина i_max в статусе GUI (таймаут 10 с).

    Ждать нужно именно ПРИЧИНУ, а не state==idle: idle наблюдается и до
    обработки sig_start worker-потоком — такой поллинг проходит раньше
    времени, и проверки output/finished читают состояние чужого старта."""
    for _ in range(100):
        if "i_max" in s.cw.status_label.text():
            break
        time.sleep(0.1)
        s.app.processEvents()
    check("i_max" in s.cw.status_label.text(),
          f"авария i_max: причина показана в GUI ({s.cw.status_label.text()!r})")
    check(s.c.state == "idle", "авария i_max: контроллер вернулся в idle")
    check(not s.k.outputA, "авария i_max: output=0")
    vacs = [f for f in list_files(os.path.join(s.workdir, "TEST4")) if f.endswith(".vac")]
    check(len(vacs) == 1, f"авария i_max: данные сохранены в папку TEST4: {vacs}")


def step_start_emergency_repeat(s: Smoke) -> None:
    """Повторный старт с тем же именем образца — та же папка пополняется."""
    s.cw.start_button.click()


def step_after_emergency_repeat(s: Smoke) -> None:
    for _ in range(100):
        vacs = [f for f in list_files(os.path.join(s.workdir, "TEST4"))
                if f.endswith(".vac")]
        if len(vacs) == 2:
            break
        time.sleep(0.1)
        s.app.processEvents()
    check(len(vacs) == 2,
          f"повторный start с тем же образцом: файлы добавлены в ту же папку: {vacs}")
    check(s.c.state == "idle", "повторная авария: контроллер в idle")


def main() -> int:
    workdir = tempfile.mkdtemp(prefix="aps_smoke_")
    app, window, controller, k2636b, ps, deviceThread = build_application(
        workdir=workdir)
    window.show()

    exit_code_holder = [0]

    def after(code: int) -> None:
        exit_code_holder[0] = code
        if code != 0:
            print("SMOKE: прерывание сценария, завершение программы")
        window.close()

    s = Smoke(app, window, controller, k2636b, ps, workdir)
    s.then(0, step_wait_init) \
        .then(100, step_config_startup) \
        .then(0, step_watcher_idle_write) \
        .then(700, step_watcher_idle_check) \
        .then(0, step_set_params_full) \
        .then(0, step_exercise_scale_and_channel) \
        .then(0, step_exercise_theme) \
        .then(0, step_start) \
        .then(150, step_points_flow) \
        .then(0, step_pause) \
        .then(300, step_paused) \
        .then(300, step_paused_frozen) \
        .then(0, step_resume) \
        .then(900, step_resumed) \
        .then(0, step_stop) \
        .then(600, step_after_stop) \
        .then(0, step_exercise_history) \
        .then(100, step_workdir_switch) \
        .then(0, step_set_params_mid) \
        .then(0, step_start_mid) \
        .then(130, step_stop_mid) \
        .then(500, step_after_stop_mid) \
        .then(100, step_set_params_short) \
        .then(0, step_start_short) \
        .then(0, step_wait_natural_end) \
        .then(100, step_set_params_emergency) \
        .then(0, step_start_emergency) \
        .then(0, step_after_emergency) \
        .then(100, step_start_emergency_repeat) \
        .then(0, step_after_emergency_repeat)

    s.run(after)

    app.exec()
    code = exit_code_holder[0]
    if FAILURES:
        code = 1
    print(f"SMOKE: завершено, код выхода {code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
