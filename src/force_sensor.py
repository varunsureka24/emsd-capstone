import time
import serial


class ForceSensorReader:
    def __init__(self, port="/dev/ttyUSB0", baudrate=115200):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.latest_value = None

    def connect(self):
        self.ser = serial.Serial(self.port, self.baudrate, timeout=0.1)
        time.sleep(2.0)
        self.ser.reset_input_buffer()

    def read_latest(self):
        if not self.ser:
            return None

        while self.ser.in_waiting:
            line = self.ser.readline().decode("ascii", errors="ignore").strip()
            if not line:
                continue

            if line.startswith("FSR:"):
                try:
                    self.latest_value = float(line.split(":", 1)[1])
                except ValueError:
                    pass
            else:
                try:
                    self.latest_value = float(line)
                except ValueError:
                    pass

    def send_command(self, cmd: str) -> None:
        if self.ser and self.ser.is_open:
            self.ser.write((cmd + "\n").encode("ascii"))

    def close(self) -> None:
        if self.ser and self.ser.is_open:
            self.ser.close()