import json
import os
import shutil

from PySide6.QtCore import QFileSystemWatcher, QObject, Signal

# Рабочая папка по умолчанию (если не передан -d и нет сохранённой в QSettings).
# Чтобы изменить место сохранения данных по умолчанию — правится этот параметр.
DEFAULT_WORKDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

CONFIG_NAME = "config.json"
LOCAL_CONFIG_NAME = "local_config.json"
DATA_EXT = ".vac"

DEFAULT_CONFIG = {
    "values": {
        "V1": -1.0,
        "V2": 1.0,
        "Vs": 0.1,
        "dt": 0.0,
        "i_max": 0.01,
        "chipType": "MD2",
        "contactA": 1,
        "contactB": 2,
        "scale": "lin",
    },
    "limits": {
        "V_min": -40.0,
        "V_max": 40.0,
        "Vs_min": 0.001,
        "Vs_max": 2.0,
        "dt_min": 0.0,
        "dt_max": 60.0,
        "i_max_limit": 0.1,
    },
}


def default_config() -> dict:
    """Свежая копия дефолтного конфига (values + limits)."""
    return {section: dict(values) for section, values in DEFAULT_CONFIG.items()}


class Session(QObject):
    """Рабочая папка запуска и её конфиги.

    <workdir>/config.json — скелет параметров (создаётся из дефолтов кода).
    <workdir>/<sample>/local_config.json — конфиг образца, создаётся при start
    копией config.json. За активным конфигом следит QFileSystemWatcher:
    ручные правки из консоли подтягиваются в GUI сигналом configChanged.
    Из GUI в JSON не пишется ничего.
    """

    configChanged = Signal(dict)

    def __init__(self, workdir: str = DEFAULT_WORKDIR, parent=None):
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._active_config: str | None = None
        self.workdir = ""
        self.set_workdir(workdir)

    @property
    def config_path(self) -> str:
        return os.path.join(self.workdir, CONFIG_NAME)

    @property
    def active_config(self) -> str:
        """Конфиг, который сейчас читает GUI (local_config образца или config.json)."""
        return self._active_config or self.config_path

    def set_workdir(self, workdir: str) -> None:
        """Сменить рабочую папку: создать её и config.json, watcher — на config.json."""
        self.workdir = os.path.abspath(workdir)
        os.makedirs(self.workdir, exist_ok=True)
        if not os.path.exists(self.config_path):
            self._write(self.config_path, default_config())
        self.set_active_config(None)

    def sample_dir(self, sample: str) -> str:
        """Папка образца <workdir>/<sample>/: создать при start, если нет.

        При первом создании внутрь кладётся local_config.json — копия config.json.
        Существующая папка пополняется, ничего не удаляется.
        """
        path = os.path.join(self.workdir, sample)
        os.makedirs(path, exist_ok=True)
        local = os.path.join(path, LOCAL_CONFIG_NAME)
        if not os.path.exists(local):
            shutil.copyfile(self.config_path, local)
        return path

    def local_config_path(self, sample: str) -> str | None:
        """Путь к local_config.json образца, если тот существует."""
        path = os.path.join(self.workdir, sample, LOCAL_CONFIG_NAME)
        return path if os.path.exists(path) else None

    def read_config(self, path: str | None = None) -> dict:
        """Прочитать конфиг; битый/неполный — дополнить дефолтами."""
        cfg = default_config()
        path = path or self.active_config
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    on_disk = json.load(f)
                for section in cfg:
                    if isinstance(on_disk.get(section), dict):
                        cfg[section].update(on_disk[section])
            except (OSError, json.JSONDecodeError) as e:
                print(f"# session: ошибка чтения {path}, используются дефолты: {e}")
        return cfg

    def set_active_config(self, path: str | None) -> None:
        """Переключить watcher на указанный конфиг (None — config.json рабочей папки)."""
        if self._active_config:
            self._watcher.removePath(self._active_config)
        self._active_config = None
        target = path or self.config_path
        if os.path.exists(target):
            self._watcher.addPath(target)
            self._active_config = target

    def _on_file_changed(self, path: str) -> None:
        """Активный конфиг изменён на диске: перечитать и уведомить GUI.

        Редакторы часто пересоздают файл — путь выпадает из watcher,
        поэтому перевешиваем его на каждое срабатывание.
        """
        if os.path.exists(path):
            self._watcher.addPath(path)
        self.configChanged.emit(self.read_config(path))

    @staticmethod
    def _write(path: str, cfg: dict) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
