"""Headless-прогон каркаса без железа и дисплея.

Запуск: QT_QPA_PLATFORM=offscreen python scripts/smoke_test.py
Проверяет: старт развёртки -> пауза -> продолжение -> стоп -> JSON.
"""

import glob
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main():
    app = QApplication([])
    win = MainWindow()

    experiment = {
        "sample": "smoke",
        "V1": -0.5, "V2": 0.5, "Vs": 0.1, "dt": 0.01,
        "contactA": 3, "contactB": 7,
    }

    done = {"path": None}
    win.controller.stopDone.connect(lambda p: done.__setitem__("path", p))

    win.sig_start.emit(experiment)
    time.sleep(0.2)
    win.sig_pause.emit()
    time.sleep(0.2)
    n_paused = len(win.controller.e["y"])
    time.sleep(0.3)
    assert len(win.controller.e["y"]) == n_paused, "пауза не сработала"
    win.sig_resume.emit()
    win.sig_stop.emit()

    t0 = time.time()
    while done["path"] is None and time.time() - t0 < 5:
        app.processEvents()
        time.sleep(0.05)

    assert done["path"], "stopDone не пришёл"
    assert os.path.exists(done["path"]), f"файл не создан: {done['path']}"
    assert n_paused > 0, "точки не измерялись"

    files = glob.glob("data/*.json")
    print(f"OK: {n_paused} точек до стопа, сохранено {done['path']}")
    print(f"файлов в data/: {len(files)}")

    # --- тест контактов: полный прогон 15 пар ---
    progress = []
    test_done = {"result": None}
    win.tester.testProgress.connect(lambda a, b, ok: progress.append((a, b, ok)))
    win.tester.testDone.connect(
        lambda aborted, n: test_done.__setitem__("result", (aborted, n)))

    win.sig_test_start.emit()
    t0 = time.time()
    while test_done["result"] is None and time.time() - t0 < 10:
        app.processEvents()
        time.sleep(0.05)

    aborted, n_checked = test_done["result"]
    assert not aborted, "тест контактов не должен был прерваться"
    assert n_checked == 15, f"проверено {n_checked}/15 пар"
    assert [p[0] for p in progress] == list(range(1, 16)), "пары не по порядку"
    assert all(p[2] for p in progress), "была неподтверждённая коммутация"
    assert win.a_spin.value() == 15 and win.b_spin.value() == 15, \
        "поля A/B не следуют за тестом"
    print(f"OK: тест контактов завершён, {n_checked}/15 пар")

    # --- тест контактов: остановка посередине ---
    progress.clear()
    test_done["result"] = None
    win.sig_test_start.emit()
    time.sleep(0.35)  # несколько пар успеет пройти
    win.sig_test_stop.emit()
    t0 = time.time()
    while test_done["result"] is None and time.time() - t0 < 10:
        app.processEvents()
        time.sleep(0.05)

    aborted, n_checked = test_done["result"]
    assert aborted, "тест должен был прерваться по stop"
    assert 0 < n_checked < 15, f"ожидалась частичная проверка, получено {n_checked}"
    print(f"OK: тест контактов прерван после {n_checked}/15 пар")

    win.close()


if __name__ == "__main__":
    main()
