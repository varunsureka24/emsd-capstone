import sys
import time
import serial
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton,
    QGridLayout, QVBoxLayout, QTextEdit, QHBoxLayout, QMessageBox
)
from PyQt5.QtCore import QTimer


GRBL_PORT = "/dev/ttyACM0"
GRBL_BAUD = 115200

DEFAULT_FEED_XY = 1200.0
DEFAULT_FEED_Z = 500.0
DEFAULT_FEED_ALL = 800.0


class GrblController:
    def __init__(self, port, baudrate=115200):
        self.port = port
        self.baudrate = baudrate
        self.ser = None

    def connect(self):
        self.ser = serial.Serial(self.port, self.baudrate, timeout=1, write_timeout=1)
        time.sleep(2.0)
        self.ser.reset_input_buffer()
        self._write_raw("\r\n\r\n")
        time.sleep(2.0)
        self.ser.reset_input_buffer()
        return self.send_command("$X")

    def _write_raw(self, s):
        self.ser.write(s.encode("ascii"))
        self.ser.flush()

    def send_command(self, cmd, expect_response=True):
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

        coords = None
        if "WPos:" in line:
            coords = line.split("WPos:")[1].split("|")[0]
        elif "MPos:" in line:
            coords = line.split("MPos:")[1].split("|")[0]

        if not coords:
            return None

        try:
            x, y, z = coords.split(",")
            return float(x), float(y), float(z)
        except:
            return None

    def jog_relative(self, dx=0.0, dy=0.0, dz=0.0, feed=800.0):
        parts = ["$J=G91"]
        if dx: parts.append(f"X{dx:.3f}")
        if dy: parts.append(f"Y{dy:.3f}")
        if dz: parts.append(f"Z{dz:.3f}")
        parts.append(f"F{feed:.1f}")
        return self.send_command(" ".join(parts))

    def feed_hold(self):
        self.ser.write(b"!")
        self.ser.flush()

    def close(self):
        if self.ser:
            self.ser.close()


class MotionGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Quick Motion Test")
        self.resize(500, 400)

        self.gc = GrblController(GRBL_PORT, GRBL_BAUD)

        self.pos_label = QLabel("Position: ---")

        self.x_input = QLineEdit("5")
        self.y_input = QLineEdit("5")
        self.z_input = QLineEdit("5")

        self.feed_xy = QLineEdit(str(DEFAULT_FEED_XY))
        self.feed_z = QLineEdit(str(DEFAULT_FEED_Z))

        self.connect_btn = QPushButton("Connect")
        self.stop_btn = QPushButton("STOP")

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)

        self._build_ui()
        self._connect()

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_pos)

    def _build_ui(self):
        layout = QVBoxLayout()

        grid = QGridLayout()
        grid.addWidget(QLabel("X (mm)"), 0, 0)
        grid.addWidget(self.x_input, 0, 1)
        grid.addWidget(QLabel("Y (mm)"), 1, 0)
        grid.addWidget(self.y_input, 1, 1)
        grid.addWidget(QLabel("Z (mm)"), 2, 0)
        grid.addWidget(self.z_input, 2, 1)

        layout.addWidget(self.pos_label)
        layout.addLayout(grid)

        row = QHBoxLayout()
        for name, func in [
            ("+X", lambda: self.move("x", 1)),
            ("-X", lambda: self.move("x", -1)),
            ("+Y", lambda: self.move("y", 1)),
            ("-Y", lambda: self.move("y", -1)),
            ("+Z", lambda: self.move("z", 1)),
            ("-Z", lambda: self.move("z", -1)),
        ]:
            btn = QPushButton(name)
            btn.clicked.connect(func)
            row.addWidget(btn)

        layout.addLayout(row)

        xyz_btn = QPushButton("Move XYZ Together")
        xyz_btn.clicked.connect(self.move_xyz)

        layout.addWidget(self.connect_btn)
        layout.addWidget(self.stop_btn)
        layout.addWidget(xyz_btn)
        layout.addWidget(self.log_box)

        self.setLayout(layout)

    def _connect(self):
        self.connect_btn.clicked.connect(self.connect_grbl)
        self.stop_btn.clicked.connect(self.feed_hold)

    def connect_grbl(self):
        try:
            resp = self.gc.connect()
            self.log("Connected")
            self.log(str(resp))
            self.timer.start(500)
        except Exception as e:
            self.log(str(e))

    def move(self, axis, sign):
        try:
            x = float(self.x_input.text())
            y = float(self.y_input.text())
            z = float(self.z_input.text())

            dx = dy = dz = 0
            if axis == "x": dx = sign * x
            if axis == "y": dy = sign * y
            if axis == "z": dz = sign * z

            feed = float(self.feed_xy.text()) if axis != "z" else float(self.feed_z.text())

            resp = self.gc.jog_relative(dx, dy, dz, feed)
            self.log(f"{axis.upper()} move -> {resp}")

        except Exception as e:
            self.log(str(e))

    def move_xyz(self):
        try:
            dx = float(self.x_input.text())
            dy = float(self.y_input.text())
            dz = float(self.z_input.text())

            resp = self.gc.jog_relative(dx, dy, dz, float(self.feed_xy.text()))
            self.log(f"XYZ move -> {resp}")

        except Exception as e:
            self.log(str(e))

    def update_pos(self):
        pos = self.gc.get_position()
        if pos:
            self.pos_label.setText(f"X={pos[0]:.2f} Y={pos[1]:.2f} Z={pos[2]:.2f}")

    def feed_hold(self):
        try:
            self.gc.feed_hold()
            self.log("STOP sent")
        except Exception as e:
            self.log(str(e))

    def log(self, msg):
        self.log_box.append(msg)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MotionGUI()
    win.show()
    sys.exit(app.exec_())