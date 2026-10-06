THEMES = ("Светлая", "Тёмная")

_QSS = {
    "Светлая": """
QWidget { background: #f5f5f5; color: #1a1a1a; }
QGroupBox { border: 1px solid #c0c0c0; border-radius: 4px; margin-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; }
QPushButton { background: #e0e0e0; border: 1px solid #b0b0b0; border-radius: 4px; padding: 4px 10px; }
QPushButton:disabled { color: #909090; }
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox { background: #ffffff; border: 1px solid #b8b8b8; border-radius: 3px; padding: 2px 4px; }
QProgressBar { border: 1px solid #b0b0b0; border-radius: 4px; text-align: center; }
QProgressBar::chunk { background: #4a90d9; }
""",
    "Тёмная": """
QWidget { background: #2b2b2b; color: #e8e8e8; }
QGroupBox { border: 1px solid #555555; border-radius: 4px; margin-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; }
QPushButton { background: #3c3c3c; border: 1px solid #5a5a5a; border-radius: 4px; padding: 4px 10px; }
QPushButton:disabled { color: #707070; }
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox { background: #3a3a3a; border: 1px solid #5a5a5a; border-radius: 3px; padding: 2px 4px; }
QProgressBar { border: 1px solid #5a5a5a; border-radius: 4px; text-align: center; }
QProgressBar::chunk { background: #3d6ea5; }
""",
}

_GRAPH = {
    "Светлая": {
        "background": "w",
        "foreground": "k",
        "curve": (40, 110, 200),
        "zero": (90, 90, 90),
    },
    "Тёмная": {
        "background": (30, 30, 30),
        "foreground": (230, 230, 230),
        "curve": (90, 160, 240),
        "zero": (160, 160, 160),
    },
}


def qss(name: str) -> str:
    """QSS-стиль темы (неизвестное имя — светлая)."""
    return _QSS.get(name, _QSS["Светлая"])


def graph_colors(name: str) -> dict:
    """Цвета графика (фон, оси, кривая, оси нуля) для темы."""
    return _GRAPH.get(name, _GRAPH["Светлая"])
