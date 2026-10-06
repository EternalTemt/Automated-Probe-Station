import os

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


class History(QWidget):
    """История измерений: сворачиваемые блоки-папки с .vag-файлами.

    Чекбокс файла включает его кривую на графике (сигнал curveToggled,
    цвет из палитры + квадрат-иконка). Блок активного запуска — наверху,
    папки пользователя — ниже. Удаление файлов с диска — с подтверждением.
    """

    curveToggled = Signal(str, bool, QColor)

    def __init__(self, session=None, parent=None):
        super().__init__(parent)
        self._session = session
        self._updating = False
        self._color_idx = 0
        self._colors: dict[str, QColor] = {}
        self._shown: dict[str, bool] = {}
        self._items: dict[str, QTreeWidgetItem] = {}
        self._blocks: dict[str, QTreeWidgetItem] = {}

        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.tree, stretch=1)

        buttons = QHBoxLayout()
        self.add_folder_button = QPushButton("Добавить папку…")
        self.delete_button = QPushButton("Удалить выбранное")
        self.add_folder_button.clicked.connect(self._on_add_folder)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        buttons.addWidget(self.add_folder_button)
        buttons.addWidget(self.delete_button)
        layout.addLayout(buttons)

        if session is not None and session.dir:
            self.add_block(session.dir, on_top=True)

    def add_block(self, dir_path: str, on_top: bool = False) -> QTreeWidgetItem:
        """Добавить блок-папку со всеми .vag внутри (новые сверху)."""
        if dir_path in self._blocks:
            return self._blocks[dir_path]
        block = QTreeWidgetItem([os.path.basename(dir_path)])
        block.setFlags(Qt.ItemFlag.ItemIsEnabled)
        if on_top:
            self.tree.insertTopLevelItem(0, block)
        else:
            self.tree.addTopLevelItem(block)
        self._blocks[dir_path] = block
        files = [f for f in os.listdir(dir_path) if f.endswith(".vag")]
        files.sort(key=lambda f: os.path.getmtime(os.path.join(dir_path, f)),
                   reverse=True)
        for name in files:
            self._add_item(block, os.path.join(dir_path, name))
        block.setExpanded(on_top)
        return block

    def add_file(self, path: str) -> None:
        """Слот на stopDone: свежий .vag — первым в блоке активного запуска."""
        if not path or not os.path.exists(path):
            return
        block = self.add_block(os.path.dirname(path), on_top=True)
        if path not in self._items:
            self._add_item(block, path, position=0)
        block.setExpanded(True)

    def mark_error(self, path: str) -> None:
        """Пометить битый .vag: подпись «(ошибка)», галочка снята."""
        item = self._items.get(path)
        if item is None:
            return
        self._shown[path] = False
        self._set_checked(item, False)
        if "(ошибка)" not in item.text(0):
            self._updating = True
            item.setText(0, item.text(0) + " (ошибка)")
            self._updating = False

    def _add_item(self, block: QTreeWidgetItem, path: str,
                  position: int | None = None) -> None:
        item = QTreeWidgetItem([os.path.basename(path)])
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                      | Qt.ItemFlag.ItemIsSelectable)
        item.setData(0, Qt.ItemDataRole.UserRole, path)
        self._set_checked(item, False)
        if position is None:
            block.addChild(item)
        else:
            block.insertChild(position, item)
        self._items[path] = item

    def _set_checked(self, item: QTreeWidgetItem, on: bool) -> None:
        self._updating = True
        item.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self._updating = False

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or item.parent() is None:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        on = item.checkState(0) == Qt.CheckState.Checked
        if self._shown.get(path, False) == on:
            return
        self._shown[path] = on
        color = self._color_for(path) if on else self._colors.get(path, QColor())
        self._updating = True
        item.setIcon(0, self._square_icon(color) if on else QIcon())
        self._updating = False
        self.curveToggled.emit(path, on, color)

    def _color_for(self, path: str) -> QColor:
        """Цвет кривой из палитры; за файлом закрепляется при первом включении."""
        if path not in self._colors:
            self._colors[path] = pg.intColor(self._color_idx, hues=9)
            self._color_idx += 1
        return self._colors[path]

    @staticmethod
    def _square_icon(color: QColor) -> QIcon:
        pm = QPixmap(12, 12)
        pm.fill(color)
        return QIcon(pm)

    def _on_add_folder(self) -> None:
        dir_path = QFileDialog.getExistingDirectory(self, "Папка с файлами .vag")
        if dir_path:
            self.add_block(dir_path, on_top=False)

    def _on_delete_clicked(self) -> None:
        paths = [item.data(0, Qt.ItemDataRole.UserRole)
                 for item in self.tree.selectedItems() if item.parent() is not None]
        if not paths:
            return
        answer = QMessageBox.question(
            self, "Удаление файлов",
            f"Удалить с диска выбранные файлы ({len(paths)} шт.)?")
        if answer == QMessageBox.StandardButton.Yes:
            self._delete(paths)

    def _delete(self, paths: list[str]) -> None:
        """Удалить файлы с диска, убрать из списка и снять кривые с графика."""
        for path in paths:
            item = self._items.pop(path, None)
            if item is None:
                continue
            if self._shown.pop(path, False):
                self.curveToggled.emit(path, False, self._colors.get(path, QColor()))
            self._colors.pop(path, None)
            item.parent().removeChild(item)
            try:
                os.remove(path)
            except OSError as e:
                print(f"# history: ошибка удаления {path}: {e}")
