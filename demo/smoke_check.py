import json
import os
import sys
import time
import traceback

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import build_application  # noqa: E402
from controller_widget import ControllerWidget  # noqa: E402
from graph import Graph  # noqa: E402

FAILURES: list[str] = []
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def check(cond: bool, what: str) -> None:
    """Условие проверки: при ложе — raise, прерываем сценарий с диагностикой."""
    if cond:
        print(f"  ok: {what}")
    else:
        raise AssertionError(what)


def list_files(sub: str) -> set:
    d = os.path.join(DATA_DIR, sub)
    return set(os.listdir(d)) if os.path.isdir(d) else set()


class Smoke:
    """Шаговый исполнитель сценария: (задержка_мс, функция) по очереди."""

    def __init__(self, app, window, controller, k2636b, ps):
        self.app = app
        self.window = window
        self.c = controller
        self.k = k2636b
        self.ps = ps
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
    s.files_before = {sub: list_files(sub) for sub in ("json", "csv", "graphs")}


def step_set_params_full(s: Smoke) -> None:
    cw = s.cw
    cw.sample_edit.setText("TEST1")
    cw.radio_md2.click()
    cw.v1_spin.setValue(-1.0)
    cw.v2_spin.setValue(1.0)
    cw.vs_spin.setValue(0.2)
    cw.dt_spin.setValue(0.01)
    cw.comp_spin.setValue(0.01)
    check(cw._ps_widget.spin_a.isEnabled(), "ячейки контактов доступны после выбора типа чипа")
    check(cw._ps_widget.spin_a.maximum() == 15, "диапазон MD2: 1..15")
    cw._ps_widget.spin_a.setValue(3)
    cw._ps_widget.spin_b.setValue(4)


def _wait_for(cond, what: str, timeout_s: float = 3.0) -> None:
    """Ждать истинности условия с таймаутом."""
    for _ in range(int(timeout_s * 20)):
        if cond():
            print(f"  ok: {what}")
            return
        time.sleep(0.05)
    raise AssertionError(what)


def step_exercise_scale_and_channel(s: Smoke) -> None:
    """Проверить связи scaleChanged и channelChanged между виджетами."""
    s.cw.radio_log.click()
    check(s.graph._log_scale, "scaleChanged -> set_log_scale(True)")
    s.cw.radio_lin.click()
    check(not s.graph._log_scale, "scaleChanged -> set_log_scale(False)")

    from actual_IV import ActualIV
    aiv = s.window.findChild(ActualIV)
    aiv.radio_b.click()
    _wait_for(lambda: s.c.channel == "B", "channelChanged -> set_channel('B')")
    check(aiv.current_channel() == "B", "actual_IV показывает канал B")
    aiv.radio_a.click()
    _wait_for(lambda: s.c.channel == "A", "channelChanged -> set_channel('A')")


def step_start(s: Smoke) -> None:
    s._n0 = s.graph.points_count()
    s.cw.start_button.click()


def step_points_flow(s: Smoke) -> None:
    check(s.c.state == "measurements", "после start контроллер в measurements")
    check(s.graph.points_count() > s._n0, "точки идут на график")
    check(s.cw.start_button.text() == "pause", "кнопка стала «pause»")
    check(not s.cw.v1_spin.isEnabled(), "поля заблокированы при измерении")
    check(s.k.outputA, "output канала A включён на время измерения")


def step_pause(s: Smoke) -> None:
    s.cw.start_button.click()


def step_paused(s: Smoke) -> None:
    check(s.c.state == "waiting", "после pause контроллер в waiting")
    check(s.cw.start_button.text() == "resume", "кнопка стала «resume»")
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
    check(s.cw.v1_spin.isEnabled(), "поля разблокированы после остановки")
    check(not s.k.outputA, "после stop output=0")

    s._new_json = None
    s._new_csv = None
    for sub in ("json", "csv", "graphs"):
        check(os.path.isdir(os.path.join(DATA_DIR, sub)),
              f"создана папка data/{sub}/")
        new = list_files(sub) - s.files_before[sub]
        check(len(new) == 1, f"появился ровно один новый файл в data/{sub}/: {new}")
        only = new.pop()
        s.files_before[sub].add(only)
        if sub == "json":
            s._new_json = only
        elif sub == "csv":
            s._new_csv = only

    s._json_path = os.path.join(DATA_DIR, "json", s._new_json)

    with open(s._json_path, encoding="utf-8") as f:
        d = json.load(f)
    p = d["params"]
    for key in ("sample", "chipType", "V1", "V2", "Vs", "dt", "compliance",
                "channel", "contactA", "contactB"):
        check(key in p, f"JSON.params содержит {key}")
    check(p["sample"] == "TEST1" and p["chipType"] == "MD2", "JSON: sample/chipType")
    check(p["contactA"] == 3 and p["contactB"] == 4, "JSON: контакты в параметрах")
    check(p["channel"] == "A", "JSON: канал развёртки A")
    check(p["V1"] == -1.0 and p["V2"] == 1.0 and p["Vs"] == 0.2 and p["dt"] == 0.01,
          "JSON: параметры развёртки")
    check(len(d["u"]) > 0 and len(d["i"]) == len(d["u"]), "JSON: массивы точек")
    check(d["graph_png"].endswith(".png") and os.path.exists(d["graph_png"]),
          "JSON: путь к существующему PNG")
    check(os.path.getsize(d["graph_png"]) > 0, "PNG не пустой")

    csv_path = os.path.join(DATA_DIR, "csv", s._new_csv)
    with open(csv_path, encoding="utf-8") as f:
        head = f.read(300)
    check("# sample=TEST1" in head and "U,I" in head, "CSV: заголовок и колонки U,I")


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
    new = list_files("json") - s.files_before["json"]
    check(len(new) == 1, f"ручной stop сохранил данные (новый JSON: {new})")
    only = new.pop()
    s.files_before["json"].add(only)
    with open(os.path.join(DATA_DIR, "json", only), encoding="utf-8") as f:
        d = json.load(f)
    check(d["params"]["sample"] == "TEST2", "JSON сегмента 2: sample=TEST2")


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
    """Поллинг до естественного перехода в idle (таймаут 15 с)."""
    for _ in range(150):
        if s.c.state == "idle":
            break
        time.sleep(0.1)
        s.app.processEvents()
    check(s.c.state == "idle", "после естественного конца развёртки — idle")
    check(s._finished_seen, "сигнал finished эмитнут при естественном конце")
    check(not s.k.outputA, "после естественного конца output=0")
    new = list_files("json") - s.files_before["json"]
    check(len(new) == 1, f"второй эксперимент сохранён (новый JSON: {new})")


def main() -> int:
    app, window, controller, k2636b, ps, deviceThread = build_application()
    window.show()

    exit_code_holder = [0]

    def after(code: int) -> None:
        exit_code_holder[0] = code
        if code != 0:
            print("SMOKE: прерывание сценария, завершение программы")
        window.close()

    s = Smoke(app, window, controller, k2636b, ps)
    s.then(0, step_wait_init) \
        .then(100, step_set_params_full) \
        .then(0, step_exercise_scale_and_channel) \
        .then(0, step_start) \
        .then(150, step_points_flow) \
        .then(0, step_pause) \
        .then(300, step_paused) \
        .then(300, step_paused_frozen) \
        .then(0, step_resume) \
        .then(600, step_resumed) \
        .then(0, step_stop) \
        .then(600, step_after_stop) \
        .then(100, step_set_params_mid) \
        .then(0, step_start_mid) \
        .then(130, step_stop_mid) \
        .then(500, step_after_stop_mid) \
        .then(100, step_set_params_short) \
        .then(0, step_start_short) \
        .then(0, step_wait_natural_end)

    s.run(after)

    app.exec()
    code = exit_code_holder[0]
    if FAILURES:
        code = 1
    print(f"SMOKE: завершено, код выхода {code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
