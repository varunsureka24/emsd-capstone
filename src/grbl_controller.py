import time
import serial


class GrblController:
    def __init__(self, port: str, baudrate: int = 115200):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self._last_wco = (0.0, 0.0, 0.0)

    def connect(self):
        self.ser = serial.Serial(self.port, self.baudrate, timeout=1, write_timeout=1)
        time.sleep(2.0)
        self.ser.reset_input_buffer()

        self._write_raw("\r\n\r\n")
        time.sleep(2.0)
        self.ser.reset_input_buffer()

        self.send_command("$X")

    def _write_raw(self, s: str):
        self.ser.write(s.encode("ascii"))
        self.ser.flush()

    def send_command(self, cmd: str, expect_response: bool = True):
        if not self.ser:
            raise RuntimeError("Not connected to GRBL")

        line = cmd.strip() + "\n"
        self._write_raw(line)

        if not expect_response:
            return []

        lines = []
        start = time.time()
        while time.time() - start < 2.0:
            if self.ser.in_waiting:
                resp = self.ser.readline().decode("ascii", errors="ignore").strip()
                if resp:
                    lines.append(resp)
                    if resp.startswith("ok") or resp.startswith("error"):
                        break
            else:
                time.sleep(0.01)
        return lines

    def query_status_line(self):
        if not self.ser:
            return None

        self.ser.reset_input_buffer()
        self._write_raw("?")
        start = time.time()
        while time.time() - start < 1.0:
            if self.ser.in_waiting:
                line = self.ser.readline().decode("ascii", errors="ignore").strip()
                if line.startswith("<"):
                    return line
            else:
                time.sleep(0.01)
        return None

    def get_position(self):
        line = self.query_status_line()
        if not line:
            return None

        try:
            if "MPos:" in line:
                coords = line.split("MPos:")[1].split("|")[0].rstrip(">")
                x, y, z = [float(v) for v in coords.split(",")]
                if "WCO:" in line:
                    wco = line.split("WCO:")[1].split("|")[0].rstrip(">")
                    ox, oy, oz = [float(v) for v in wco.split(",")]
                    self._last_wco = (ox, oy, oz)
                ox, oy, oz = self._last_wco
                return x - ox, y - oy, z - oz

            if "WPos:" in line:
                wpos = line.split("WPos:")[1].split("|")[0].rstrip(">")
                wx, wy, wz = [float(v) for v in wpos.split(",")]
                if "WCO:" in line:
                    wco = line.split("WCO:")[1].split("|")[0].rstrip(">")
                    self._last_wco = tuple(float(v) for v in wco.split(","))
                return wx, wy, wz
        except Exception:
            return None

        return None

    def get_machine_state(self):
        line = self.query_status_line()
        if not line:
            return None
        try:
            return line.split("|")[0].strip("<>")
        except Exception:
            return None

    def jog(self, dx=0.0, dy=0.0, dz=0.0, feed=500.0):
        cmd = "$J=G91 G21"
        if dx != 0.0:
            cmd += f" X{dx:.3f}"
        if dy != 0.0:
            cmd += f" Y{dy:.3f}"
        if dz != 0.0:
            cmd += f" Z{dz:.3f}"
        cmd += f" F{feed:.1f}"
        if self.ser and self.ser.in_waiting:
            self.ser.read(self.ser.in_waiting)
        return self.send_command(cmd, expect_response=False)

    def jog_cancel(self):
        # 0x85 is GRBL's real-time jog-cancel byte: decelerates to a stop
        # without triggering an alarm, and flushes queued jog segments
        if self.ser:
            self.ser.write(b"\x85")
            self.ser.flush()

    def home(self, timeout: float = 60.0):
        if not self.ser:
            return []
        self._write_raw("$H\n")
        lines = []
        start = time.time()
        while time.time() - start < timeout:
            if self.ser.in_waiting:
                resp = self.ser.readline().decode("ascii", errors="ignore").strip()
                if resp:
                    lines.append(resp)
                    if resp.startswith("ok") or resp.startswith("error"):
                        break
            else:
                time.sleep(0.05)
        return lines

    def move_to(self, x=None, y=None, z=None, feed=3000.0):
        parts = ["G21", "G1"]
        if x is not None:
            parts.append(f"X{x:.3f}")
        if y is not None:
            parts.append(f"Y{y:.3f}")
        if z is not None:
            parts.append(f"Z{z:.3f}")
        if feed is not None:
            parts.append(f"F{feed:.1f}")
        cmd = " ".join(parts)
        return self.send_command(cmd)

    def feed_hold(self):
        if self.ser:
            self.ser.write(b"!")
            self.ser.flush()

    def cycle_start(self):
        if self.ser:
            self.ser.write(b"~")
            self.ser.flush()

    def soft_reset(self):
        if self.ser:
            self.ser.write(b"\x18")
            self.ser.flush()

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            self.ser = None