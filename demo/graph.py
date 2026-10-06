import os

import pyqtgraph as pg
from PySide6.QtWidgets import QVBoxLayout, QWidget


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

        zero_pen = pg.mkPen(color=(90, 90, 90), width=2)
        self._zero_x = pg.InfiniteLine(angle=0, pen=zero_pen)
        self._zero_y = pg.InfiniteLine(angle=90, pen=zero_pen)
        self.plot_widget.addItem(self._zero_x)
        self.plot_widget.addItem(self._zero_y)

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
        self.curve.setData(self._u, self._i)

    def set_log_scale(self, log_scale: bool) -> None:
        """Переключение шкалы тока: False — линейная, True — логарифмическая (только вид оси)."""
        self._log_scale = log_scale
        self.plot_widget.getPlotItem().setLogMode(x=False, y=log_scale)

    def clear(self) -> None:
        """Очистка графика (новый старт)."""
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

    def apply_theme(self, colors: dict) -> None:
        """Цвета фона, осей, кривой и осей нуля под выбранную тему."""
        self.plot_widget.setBackground(colors["background"])
        for name in ("bottom", "left"):
            axis = self.plot_widget.getPlotItem().getAxis(name)
            axis.setPen(pg.mkPen(color=colors["foreground"]))
            axis.setTextPen(pg.mkPen(color=colors["foreground"]))
        self.curve.setPen(pg.mkPen(color=colors["curve"], width=2))
        self.curve.setSymbolBrush(colors["curve"])
        zero_pen = pg.mkPen(color=colors["zero"], width=2)
        self._zero_x.setPen(zero_pen)
        self._zero_y.setPen(zero_pen)

    @staticmethod
    def _png_path_from_json(json_path: str) -> str:
        """.../data/json/<имя>.json -> .../data/graphs/<имя>.png"""
        base = os.path.splitext(json_path)[0]
        head, tail = os.path.split(base)
        return os.path.join(os.path.dirname(head), "graphs", tail + ".png")
