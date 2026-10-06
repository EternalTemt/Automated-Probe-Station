import json
import os
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

DEFAULT_CONFIG = {
    "values": {
        "sample": "",
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


class Session:
    """Папка запуска demo/data/<YYYY-MM-DD_HH-MM-SS>/ и её config.json.

    values — рабочие значения (обмен через set/load default),
    limits — жёсткие границы полей GUI, правятся только из консоли.
    """

    def __init__(self, data_dir: str = DATA_DIR):
        self._data_dir = data_dir
        self.dir: str | None = None

    def start_run(self) -> str:
        """Создать папку запуска со свежим config.json, вернуть её путь."""
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(self._data_dir, stamp)
        n = 0
        while os.path.exists(path):
            n += 1
            path = os.path.join(self._data_dir, f"{stamp}_{n}")
        os.makedirs(path)
        self.dir = path
        self._write(DEFAULT_CONFIG)
        return path

    def read_config(self) -> dict:
        """Прочитать config.json; битый/неполный — дополнить дефолтами."""
        cfg = {section: dict(defaults) for section, defaults in DEFAULT_CONFIG.items()}
        path = self._config_path()
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    on_disk = json.load(f)
                for section in cfg:
                    if isinstance(on_disk.get(section), dict):
                        cfg[section].update(on_disk[section])
            except (OSError, json.JSONDecodeError) as e:
                print(f"# session: ошибка чтения config.json, используются дефолты: {e}")
        return cfg

    def write_values(self, values: dict) -> None:
        """set default: перезаписать values, сохранив limits."""
        cfg = self.read_config()
        cfg["values"].update(values)
        self._write(cfg)

    def _config_path(self) -> str | None:
        return os.path.join(self.dir, "config.json") if self.dir else None

    def _write(self, cfg: dict) -> None:
        with open(self._config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
