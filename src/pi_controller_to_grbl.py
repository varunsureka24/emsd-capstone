import time
import pygame
import serial

# ================== CONFIG ==================

# Likely Arduino port on the Pi:
#   /dev/ttyACM0 (common for Uno)
#   /dev/ttyUSB0 (common for some clones)
GRBL_PORT = "/dev/ttyACM0" 
GRBL_BAUD = 115200

# Joystick behavior
DEADZONE = 0.2          # how far you need to push stick before it counts
MAX_STEP_MM = 1.0       # max incremental move per command (in mm)
FEED_MM_PER_MIN = 500.0 # jog feed rate
COMMAND_INTERVAL = 0.2  # seconds between jog commands

# ============================================


class GrblController:
    def __init__(self, port: str, baudrate: int = 115200):
        self.port = port
        self.baudrate = baudrate
        self.ser = None

    def connect(self):
        print(f"Opening GRBL serial port {self.port} @ {self.baudrate}...")
        self.ser = serial.Serial(self.port, self.baudrate, timeout=1, write_timeout=1)

        # Let GRBL reset
        time.sleep(2.0)

        # Clear any junk
        self.ser.reset_input_buffer()

        # Wake up GRBL
        self._write_raw("\r\n\r\n")
        time.sleep(2.0)
        self.ser.reset_input_buffer()

        # Try unlock in case it's in alarm
        print("Sending $X to unlock (if in alarm)...")
        resp = self.send_command("$X")
        print("Unlock response:", resp)

    def _write_raw(self, s: str):
        self.ser.write(s.encode("ascii"))
        self.ser.flush()

    def send_command(self, cmd: str, expect_response: bool = True):
        """
        Send a single line command and collect responses until 'ok' or 'error' or timeout.
        Returns a list of response lines.
        """
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
                    # Most GRBL commands end with one of these
                    if resp.startswith("ok") or resp.startswith("error"):
                        break
            else:
                time.sleep(0.01)
        return lines

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            self.ser = None

def main():
    # ---------- Connect to GRBL ----------
    gc = GrblController(GRBL_PORT, GRBL_BAUD)
    try:
        gc.connect()
    except Exception as e:
        print("Failed to connect to GRBL:", e)
        return

    # Optional: show a few lines of settings
    print("Requesting GRBL settings ($$)...")
    settings = gc.send_command("$$")
    print("\n".join(settings[:10]), "...\n")

    # ---------- Init Controller ----------
    pygame.init()
    pygame.joystick.init()

    if pygame.joystick.get_count() == 0:
        print("No controller detected on Pi. Plug it in via USB.")
        gc.close()
        return

    js = pygame.joystick.Joystick(0)
    js.init()
    print(f"Controller connected: {js.get_name()}")
    print("Controls:")
    print("  D-pad         → X/Y jog")
    print("  A button (0)  → +Z jog")
    print("  B button (1)  → -Z jog")
    print("  (Incremental moves using $J=G91 ...)")
    print("Press Ctrl+C to quit.\n")

    last_cmd_time = 0.0

    try:
        while True:
            # Process events so pygame stays happy
            pygame.event.pump()

            # ----- Read controls -----
            # D-pad is usually hat 0 on Xbox-style controllers
            hat_x, hat_y = js.get_hat(0)
            # hat_x: -1 left, 0 center, +1 right
            # hat_y: -1 down, 0 center, +1 up

            # Buttons for Z (typical Xbox mapping)
            a_pressed = js.get_button(0)  # A button
            b_pressed = js.get_button(1)  # B button

            now = time.time()
            if now - last_cmd_time >= COMMAND_INTERVAL:
                # X/Y from D-pad (discrete steps)
                dx = MAX_STEP_MM * hat_x        # -1, 0, or +1 times step
                dy = MAX_STEP_MM * hat_y        # -1, 0, or +1 times step

                # Z from buttons
                dz = 0.0
                if a_pressed:
                    dz = MAX_STEP_MM   # +Z (up)
                elif b_pressed:
                    dz = -MAX_STEP_MM  # -Z (down)

                if dx != 0.0 or dy != 0.0 or dz != 0.0:
                    parts = ["$J=G91"]
                    if dx != 0.0:
                        parts.append(f"X{dx:.3f}")
                    if dy != 0.0:
                        parts.append(f"Y{dy:.3f}")
                    if dz != 0.0:
                        parts.append(f"Z{dz:.3f}")
                    parts.append(f"F{FEED_MM_PER_MIN:.1f}")

                    gcode = " ".join(parts)
                    print("Sending:", gcode)
                    resp = gc.send_command(gcode)
                    print("  GRBL:", resp)

                    last_cmd_time = now

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nExiting...")

    finally:
        gc.close()
        pygame.joystick.quit()
        pygame.quit()


if __name__ == "__main__":
    main()