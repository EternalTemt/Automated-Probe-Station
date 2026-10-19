import os

import pyqtgraph as pg
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

PATH_ROLE = Qt.ItemDataRole.UserRole
TYPE_ROLE = Qt.ItemDataRole.UserRole + 1
TYPE_DIR = "dir"
TYPE_FILE = "file"
TYPE_PLACEHOLDER = "placeholder"

DATA_EXT = ".vac"


class History(QWidget):
    """История измерений: дерево папок рабочей директории с .vac-файлами.

    Папки подгружаются лениво: при старте добавляются без сканирования
    вглубь, содержимое (.vac + подпапки) читается при раскрытии блока.
    Галочка файла строит его кривую на графике (curveToggled, цвет из
    палитры + квадрат-иконка). Контекстное меню папки: «Сделать основной»
    (блок наверх, watcher на её local_config) и «Удалить из Истории»
    (только из GUI); меню файла: «Удалить с устройства» с подтверждением.
    """

    curveToggled = Signal(str, bool, QColor)
    primaryChanged = Signal(str)
    workdirChanged = Signal(str)

    def __init__(self, workdir: str | None = None, parent=None):
        super().__init__(parent)
        self._workdir: str | None = None
        self._updating = False
        self._color_idx = 0
        self._colors: dict[str, QColor] = {}
        self._shown: dict[str, bool] = {}
        self._items: dict[str, QTreeWidgetItem] = {}
        self._blocks: dict[str, QTreeWidgetItem] = {}
        self._primary_dir: str | None = None

        layout = QVBoxLayout(self)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск")
        self.search_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_edit)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self.tree, stretch=1)

        buttons = QHBoxLayout()
        self.add_folder_button = QPushButton("Добавить папку…")
        self.workdir_button = QPushButton("Рабочая папка…")
        self.add_folder_button.clicked.connect(self._on_add_folder)
        self.workdir_button.clicked.connect(self._on_change_workdir)
        buttons.addWidget(self.add_folder_button)
        buttons.addWidget(self.workdir_button)
        layout.addLayout(buttons)

        if workdir:
            self.set_workdir(workdir)

    # --- построение дерева ---

    def set_workdir(self, dir_path: str) -> None:
        """Сменить рабочую папку: перестроить дерево, снять показанные кривые."""
        for path, shown in list(self._shown.items()):
            if shown:
                self.curveToggled.emit(path, False, self._colors.get(path, QColor()))
        self._shown.clear()
        self.tree.clear()
        self._items.clear()
        self._blocks.clear()
        self._primary_dir = None
        self._workdir = dir_path

        for sub in self._subdirs(dir_path):
            self._add_block(sub)
        for name in self._vac_files(dir_path):
            self._make_file_item(os.path.join(dir_path, name))
        self._resort()

    @staticmethod
    def _subdirs(dir_path: str) -> list[str]:
        try:
            subs = [os.path.join(dir_path, n) for n in os.listdir(dir_path)
                    if os.path.isdir(os.path.join(dir_path, n))]
        except OSError:
            return []
        return sorted(subs, key=lambda p: os.path.basename(p).lower())

    @staticmethod
    def _vac_files(dir_path: str) -> list[str]:
        """.vac-файлы папки (без вложенности), новые по mtime первыми."""
        try:
            files = [f for f in os.listdir(dir_path) if f.endswith(DATA_EXT)]
        except OSError:
            return []
        files.sort(key=lambda f: os.path.getmtime(os.path.join(dir_path, f)),
                   reverse=True)
        return files

    def _make_dir_item(self, dir_path: str) -> QTreeWidgetItem:
        """Ленивый блок-папка: плейсхолдер вместо содержимого до раскрытия."""
        block = QTreeWidgetItem([os.path.basename(dir_path)])
        block.setFlags(Qt.ItemFlag.ItemIsEnabled)
        block.setData(0, PATH_ROLE, dir_path)
        block.setData(0, TYPE_ROLE, TYPE_DIR)
        placeholder = QTreeWidgetItem(["…"])
        placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
        placeholder.setData(0, TYPE_ROLE, TYPE_PLACEHOLDER)
        block.addChild(placeholder)
        self._blocks[dir_path] = block
        return block

    def _make_file_item(self, path: str,
                        parent: QTreeWidgetItem | None = None) -> QTreeWidgetItem:
        item = QTreeWidgetItem([os.path.basename(path)])
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                      | Qt.ItemFlag.ItemIsSelectable)
        item.setData(0, PATH_ROLE, path)
        item.setData(0, TYPE_ROLE, TYPE_FILE)
        self._set_checked(item, False)
        if parent is not None:
            parent.addChild(item)
        self._items[path] = item
        return item

    def _add_block(self, dir_path: str) -> QTreeWidgetItem:
        if dir_path in self._blocks:
            return self._blocks[dir_path]
        block = self._make_dir_item(dir_path)
        self.tree.addTopLevelItem(block)
        self._resort()
        return block

    def _scan_block(self, block: QTreeWidgetItem) -> None:
        """Раскрытие ленивого блока: прочитать .vac и подпапки (один уровень)."""
        if block.childCount() != 1 or \
                block.child(0).data(0, TYPE_ROLE) != TYPE_PLACEHOLDER:
            return
        dir_path = block.data(0, PATH_ROLE)
        block.removeChild(block.child(0))
        for sub in self._subdirs(dir_path):
            if sub not in self._blocks:
                block.addChild(self._make_dir_item(sub))
        for name in self._vac_files(dir_path):
            path = os.path.join(dir_path, name)
            if path not in self._items:
                self._make_file_item(path, parent=block)
        self._apply_filter(self.search_edit.text())

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        if item.data(0, TYPE_ROLE) == TYPE_DIR:
            self._scan_block(item)

    def _resort(self) -> None:
        """Порядок верхнего уровня: основная папка, остальные по алфавиту, затем файлы."""
        primary, dirs, files = None, [], []
        while self.tree.topLevelItemCount():
            item = self.tree.takeTopLevelItem(0)
            if item.data(0, TYPE_ROLE) == TYPE_DIR:
                if item.data(0, PATH_ROLE) == self._primary_dir:
                    primary = item
                else:
                    dirs.append(item)
            else:
                files.append(item)
        dirs.sort(key=lambda it: it.text(0).lower())
        files.sort(key=lambda it: it.text(0).lower())
        for item in ([primary] if primary else []) + dirs + files:
            self.tree.addTopLevelItem(item)

    def _move_block_to_top(self, dir_path: str) -> None:
        self._primary_dir = dir_path
        self._resort()

    # --- слоты сохранения и ошибок ---

    def on_measurement_saved(self, path: str) -> None:
        """Слот на stopDone: файл первым в блоке, блок наверх, галочка прожата."""
        if not path or not os.path.exists(path):
            return
        dir_path = os.path.dirname(path)
        block = self._add_block(dir_path)
        self._move_block_to_top(dir_path)
        self._scan_block(block)
        item = self._items.get(path)
        if item is None:
            item = self._make_file_item(path)
            block.insertChild(0, item)
        block.setExpanded(True)
        item.setCheckState(0, Qt.CheckState.Checked)

    def add_file(self, path: str) -> None:
        """Добавить файл в дерево (без авто-галочки; используется smoke_check)."""
        if not path or not os.path.exists(path) or path in self._items:
            return
        block = self._add_block(os.path.dirname(path))
        self._scan_block(block)
        if path not in self._items:
            block.insertChild(0, self._make_file_item(path))

    def mark_error(self, path: str) -> None:
        """Пометить битый .vac: подпись «(ошибка)», галочка снята."""
        item = self._items.get(path)
        if item is None:
            return
        self._shown[path] = False
        self._set_checked(item, False)
        if "(ошибка)" not in item.text(0):
            self._updating = True
            item.setText(0, item.text(0) + " (ошибка)")
            self._updating = False

    # --- галочки и кривые ---

    def _set_checked(self, item: QTreeWidgetItem, on: bool) -> None:
        self._updating = True
        item.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self._updating = False

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or item.data(0, TYPE_ROLE) != TYPE_FILE:
            return
        path = item.data(0, PATH_ROLE)
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

    # --- поиск ---

    def _apply_filter(self, text: str) -> None:
        """Подстрочный фильтр: папка видна, если совпало её имя или имя файла внутри."""
        needle = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if not needle:
                item.setHidden(False)
                continue
            match = needle in item.text(0).lower()
            if not match and item.data(0, TYPE_ROLE) == TYPE_DIR:
                match = any(
                    item.child(j).data(0, TYPE_ROLE) == TYPE_FILE
                    and needle in item.child(j).text(0).lower()
                    for j in range(item.childCount()))
            item.setHidden(not match)

    # --- кнопки и контекстное меню ---

    def _on_add_folder(self) -> None:
        dir_path = QFileDialog.getExistingDirectory(self, "Папка с файлами .vac")
        if dir_path:
            self._add_block(dir_path)

    def _on_change_workdir(self) -> None:
        dir_path = QFileDialog.getExistingDirectory(
            self, "Рабочая папка", self._workdir or "")
        if dir_path:
            self.set_workdir(dir_path)
            self.workdirChanged.emit(dir_path)

    def _on_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        item_type = item.data(0, TYPE_ROLE)
        menu = QMenu(self)
        if item_type == TYPE_DIR:
            dir_path = item.data(0, PATH_ROLE)
            action_primary = menu.addAction("Сделать основной")
            action_forget = menu.addAction("Удалить из Истории")
            action_open = menu.addAction("Открыть в проводнике")
            chosen = self._exec_menu(menu, pos)
            if chosen is action_primary:
                self._move_block_to_top(dir_path)
                item.setExpanded(True)
                self.primaryChanged.emit(dir_path)
            elif chosen is action_forget:
                self._forget_block(item)
            elif chosen is action_open:
                self._open_in_explorer(dir_path)
        elif item_type == TYPE_FILE:
            path = item.data(0, PATH_ROLE)
            action_delete = menu.addAction("Удалить с устройства")
            chosen = self._exec_menu(menu, pos)
            if chosen is action_delete:
                answer = QMessageBox.question(
                    self, "Удаление файла",
                    f"Удалить с диска файл «{os.path.basename(path)}»?")
                if answer == QMessageBox.StandardButton.Yes:
                    self._delete([path])

    def _exec_menu(self, menu: QMenu, pos):
        """Показать контекстное меню (вынесено для подмены в smoke_check)."""
        return menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _open_in_explorer(self, dir_path: str) -> None:
        """Открыть папку в файловом менеджере ОС."""
        QDesktopServices.openUrl(QUrl.fromLocalFile(dir_path))

    def _forget_block(self, item: QTreeWidgetItem) -> None:
        """«Удалить из Истории»: убрать папку из дерева, диск не трогаем."""
        dir_path = item.data(0, PATH_ROLE)
        for j in range(item.childCount()):
            child = item.child(j)
            if child.data(0, TYPE_ROLE) == TYPE_FILE:
                path = child.data(0, PATH_ROLE)
                self._items.pop(path, None)
                if self._shown.pop(path, False):
                    self.curveToggled.emit(path, False,
                                           self._colors.get(path, QColor()))
        self._blocks.pop(dir_path, None)
        if self._primary_dir == dir_path:
            self._primary_dir = None
        self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))

    def _delete(self, paths: list[str]) -> None:
        """Удалить файлы с диска, убрать из списка и снять кривые с графика."""
        for path in paths:
            item = self._items.pop(path, None)
            if item is None:
                continue
            if self._shown.pop(path, False):
                self.curveToggled.emit(path, False, self._colors.get(path, QColor()))
            self._colors.pop(path, None)
            parent = item.parent()
            if parent is not None:
                parent.removeChild(item)
            else:
                self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
            try:
                os.remove(path)
            except OSError as e:
                print(f"# history: ошибка удаления {path}: {e}")
