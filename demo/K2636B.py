import random
import math

import pyvisa
from PySide6.QtCore import QObject, Signal

try:
    import usb.core
except ImportError:
    usb = None


class K2636B(QObject):
    """Драйвер Keithley 2636B: два независимых SMU-канала (smua, smub)."""

    debug: bool
    opened: bool
    simulated: bool

    currentA: float
    voltageA: float
    outputA: bool
    currentB: float
    voltageB: float
    outputB: bool

    newIV_A = Signal(tuple)
    newIV_B = Signal(tuple)
    newOut_A = Signal(bool)
    newOut_B = Signal(bool)
    connectionLost = Signal()
    error = Signal(str)

    def __init__(self, parent=None, debug: bool = False):
        super().__init__(parent)
        self.debug = debug
        self.opened = False
        self.simulated = False

        self.currentA = 0.0
        self.voltageA = 0.0
        self.outputA = False
        self.currentB = 0.0
        self.voltageB = 0.0
        self.outputB = False

        self._complianceA = 0.1
        self._complianceB = 0.1

        self._sim_last_u = {"A": 0.0, "B": 0.0}
        self._sim_dir = {"A": 1, "B": 1}

    def open(self) -> None:
        """Открыть прибор через pyvisa; при неудаче — перейти в симуляцию."""
        if self.debug:
            print("# K2636B: open")

        try:
            self.rm = pyvisa.ResourceManager()
            resources = [r for r in self.rm.list_resources() if r.startswith("USB")]
            if not resources:
                raise RuntimeError("USBTMC-ресурсы Keithley не найдены")
            self.k = self.rm.open_resource(resources[0])
            self.k.timeout = 5000
            self._write_real("smua.source.output = smua.OUTPUT_OFF\n")
            self._write_real("smub.source.output = smub.OUTPUT_OFF\n")
            for ch in ("a", "b"):
                self._write_real(f"smu{ch}.source.func = smu{ch}.OUTPUT_DCVOLTS\n")
                self._write_real(f"smu{ch}.source.autorangev = smu{ch}.AUTORANGE_ON\n")
                self._write_real(f"smu{ch}.source.autorangei = smu{ch}.AUTORANGE_ON\n")
                self._write_real(f"smu{ch}.source.levelv = 0.0\n")
                self._write_real(f"smu{ch}.measure.filter.count = 3\n")
                self._write_real(f"smu{ch}.measure.filter.type = smu{ch}.FILTER_REPEAT_AVG\n")
                self._write_real(f"smu{ch}.measure.filter.enable = smu{ch}.FILTER_OFF\n")
                self._write_real(f"smu{ch}.measure.nplc = 1\n")
            self.opened = True
            self.simulated = False
            if self.debug:
                print(f"# K2636B: открыт {resources[0]}")
        except Exception as e:
            print(f"# K2636B: прибор не найден ({e}); включён режим симуляции")
            self.opened = True
            self.simulated = True
            self.outputA = False
            self.outputB = False

    def close(self) -> None:
        """Выключить output, обнулить напряжение и освободить ресурс pyvisa."""
        if self.debug:
            print("# K2636B: close")
        if not self.opened:
            print("# K2636B err: not opened")
            return

        if self.simulated:
            self.outputA = False
            self.outputB = False
            self.voltageA = 0.0
            self.voltageB = 0.0
            self.opened = False
            return

        try:
            self._write_real("smua.source.output = smua.OUTPUT_OFF\n")
            self._write_real("smub.source.output = smub.OUTPUT_OFF\n")
            self._write_real("smua.source.levelv = 0\n")
            self._write_real("smub.source.levelv = 0\n")
            self.k.close()
            self.rm.close()
        except Exception as e:
            print(f"# K2636B err при закрытии: {e}")
        finally:
            self._reattach_usb_driver()
            self.opened = False

    def _reattach_usb_driver(self) -> None:
        """Вернуть kernel-драйвер USBTMC (как в референсе) — только Linux."""
        if usb is None:
            return
        try:
            dev = usb.core.find(idVendor=0x05E6, idProduct=0x2636)
            if dev is not None:
                try:
                    dev.attach_kernel_driver(0)
                except Exception:
                    pass
        except Exception:
            pass

    def _write_real(self, cmd: str) -> None:
        """Запись команды в реальный прибор с контролем потери соединения."""
        try:
            if self.debug:
                print(f"# K2636B: write: {cmd.encode()}")
            self.k.write(cmd)
        except Exception:
            self.connectionLost.emit()
            raise

    def _read_real(self) -> str:
        """Чтение ответа реального прибора с контролем потери соединения."""
        try:
            r = self.k.read()
            if self.debug:
                print(f"# K2636B: read: {r.encode()}")
            return r
        except Exception:
            self.connectionLost.emit()
            raise

    def set_output_A(self, output: bool) -> None:
        self._set_output("A", output)

    def set_output_B(self, output: bool) -> None:
        self._set_output("B", output)

    def _set_output(self, ch: str, output: bool) -> None:
        """Установить output и ПЕРЕПРОВЕРИТЬ фактический статус с прибора."""
        if self.debug:
            print(f"# K2636B: set_output_{ch}({output})")
        if not self.opened:
            print("# K2636B err: not opened")
            return

        if not self.simulated:
            try:
                self._write_real(f"smu{ch.lower()}.source.output = {'1' if output else '0'}\n")
            except Exception:
                return

        if ch == "A":
            self.outputA = output
        else:
            self.outputB = output

        actual = self.get_output_A() if ch == "A" else self.get_output_B()
        if actual != output:
            self.error.emit(
                f"K2636B: канал {ch}: запрошен output={output}, прибор вернул {actual}"
            )

    def get_output_A(self) -> bool:
        return self._get_output("A")

    def get_output_B(self) -> bool:
        return self._get_output("B")

    def _get_output(self, ch: str) -> bool:
        if self.debug:
            print(f"# K2636B: get_output_{ch}")
        if not self.opened:
            print("# K2636B err: not opened")
            return False

        if self.simulated:
            actual = self.outputA if ch == "A" else self.outputB
        else:
            try:
                self._write_real(f"print(smu{ch.lower()}.source.output)\n")
                actual = bool(float(self._read_real()))
            except Exception:
                return False
            if ch == "A":
                self.outputA = actual
            else:
                self.outputB = actual

        if ch == "A":
            self.newOut_A.emit(actual)
        else:
            self.newOut_B.emit(actual)
        return actual

    def set_voltage_A(self, voltage: float) -> None:
        self._set_voltage("A", voltage)

    def set_voltage_B(self, voltage: float) -> None:
        self._set_voltage("B", voltage)

    def _set_voltage(self, ch: str, voltage: float) -> None:
        """Задать уровень напряжения на канале (источник DCV)."""
        if self.debug:
            print(f"# K2636B: set_voltage_{ch}({voltage})")
        if not self.opened:
            print("# K2636B err: not opened")
            return

        if not self.simulated:
            try:
                self._write_real(f"smu{ch.lower()}.source.levelv = {voltage:.4f}\n")
            except Exception:
                return

        if ch == "A":
            self.voltageA = voltage
        else:
            self.voltageB = voltage

    def get_iv_A(self) -> tuple[float, float]:
        return self._get_iv("A")

    def get_iv_B(self) -> tuple[float, float]:
        return self._get_iv("B")

    def _get_iv(self, ch: str) -> tuple[float, float]:
        """Снять пару (i, v) с канала и разослать её подписчикам (newIV_*)."""
        if self.debug:
            print(f"# K2636B: get_iv_{ch}")
        if not self.opened:
            print("# K2636B err: not opened")
            return (0.0, 0.0)

        if self.simulated:
            u = self.voltageA if ch == "A" else self.voltageB
            iv = self._sim_iv(ch, u)
        else:
            try:
                self._write_real(f"print(smu{ch.lower()}.measure.iv())\n")
                iv = tuple(map(float, self._read_real().split()))
            except Exception:
                return (0.0, 0.0)

        if ch == "A":
            self.currentA, self.voltageA = iv[0], iv[1]
            self.newIV_A.emit(iv)
        else:
            self.currentB, self.voltageB = iv[0], iv[1]
            self.newIV_B.emit(iv)
        return iv

    def set_compliance_A(self, limit: float) -> None:
        self._set_compliance("A", limit)

    def set_compliance_B(self, limit: float) -> None:
        self._set_compliance("B", limit)

    def _set_compliance(self, ch: str, limit: float) -> None:
        """Установить ограничение тока канала (команда smuX.source.limiti)."""
        if self.debug:
            print(f"# K2636B: set_compliance_{ch}({limit})")
        if not self.opened:
            print("# K2636B err: not opened")
            return

        if not self.simulated:
            try:
                self._write_real(f"smu{ch.lower()}.source.limiti = {limit:.6e}\n")
            except Exception:
                return

        if ch == "A":
            self._complianceA = limit
        else:
            self._complianceB = limit

    def _sim_iv(self, ch: str, u: float) -> tuple[float, float]:
        """Правдоподобная модель ВАХ для работы без прибора."""
        if u > self._sim_last_u[ch] + 1e-12:
            self._sim_dir[ch] = 1
        elif u < self._sim_last_u[ch] - 1e-12:
            self._sim_dir[ch] = -1
        self._sim_last_u[ch] = u

        asym = 1.0 + 0.07 * self._sim_dir[ch]
        i = 0.05 * u * abs(u) * asym
        i += random.gauss(0.0, 1e-5 * max(abs(i), 1e-9))

        limit = self._complianceA if ch == "A" else self._complianceB
        if abs(i) > limit:
            i = math.copysign(limit, i)
        return (i, u)
