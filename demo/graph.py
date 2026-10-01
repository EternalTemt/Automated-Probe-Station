import math
import os

import pyqtgraph as pg
from PySide6.QtWidgets import QVBoxLayout, QWidget

LOG_FLOOR = 1e-12


class Graph(QWidget):
    """График ВАХ: накапливает точки и рисует I(U) в лин/лог масштабе."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._u: list[float] = []
        self._i: list[float] = []
        self._log_scale = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setMinimumSize(400, 300)
        self.plot_widget.setLabel("bottom", "U", units="В")
        self.plot_widget.setLabel("left", "I", units="А")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        layout.addWidget(self.plot_widget)

        self.curve = self.plot_widget.plot(
            [], [],
            pen=pg.mkPen(color=(40, 110, 200), width=2),
            symbol="o",
            symbolSize=5,
            symbolBrush=(40, 110, 200),
        )

    def add_point(self, u: float, i: float) -> None:
        """Новая точка развёртки от контроллера."""
        self._u.append(u)
        self._i.append(i)
        self._redraw()

    def set_log_scale(self, log_scale: bool) -> None:
        """Переключение шкалы тока: False — линейная, True — log|I|."""
        self._log_scale = log_scale
        if log_scale:
            self.plot_widget.setLabel("left", "log|I|", units="А")
        else:
            self.plot_widget.setLabel("left", "I", units="А")
        self._redraw()

    def clear(self) -> None:
        """Очистка графика (restart / новый старт)."""
        self._u.clear()
        self._i.clear()
        self.curve.setData([], [])

    def on_experiment_finished(self, json_path: str) -> None:
        """Сохранение PNG графика рядом с JSON при завершении измерения."""
        if not json_path:
            return
        png_path = self._png_path_from_json(json_path)
        os.makedirs(os.path.dirname(png_path), exist_ok=True)
        self.plot_widget.grab().save(png_path, "PNG")

    def points_count(self) -> int:
        """Число накопленных точек (используется smoke_check)."""
        return len(self._u)

    def _redraw(self) -> None:
        if self._log_scale:
            y = [math.log10(max(abs(v), LOG_FLOOR)) for v in self._i]
        else:
            y = self._i
        self.curve.setData(self._u, y)

    @staticmethod
    def _png_path_from_json(json_path: str) -> str:
        """.../data/json/sample_X_<время>.json -> .../data/graphs/sample_X_<время>.png"""
        base = os.path.splitext(json_path)[0]
        head, tail = os.path.split(base)
        return os.path.join(os.path.dirname(head), "graphs", tail + ".png")
