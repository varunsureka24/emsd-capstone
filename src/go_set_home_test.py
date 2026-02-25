import time
import pygame
import serial

# ================== CONFIG ==================

GRBL_PORT = "/dev/ttyACM0"
GRBL_BAUD = 115200

MAX_STEP_MM = 5.0        # jog increment per press (mm)
FEED_MM_PER_MIN = 2000  # jog feed rate
COMMAND_INTERVAL = 0.2   # seconds between jog commands

# ============================================


class GrblController:
    def __init__(self, port, baudrate=115200):
        self.ser = serial.Serial(port, baudrate, timeout=1, write_timeout=1)
        time.sleep(2.0)
        self.ser.reset_input_buffer()
        self._write_raw("\r\n\r\n")
        time.sleep(2.0)
        self.ser.reset_input_buffer()
        self.send_command("$X")  # unlock if needed

    def _write_raw(self, s):
        self.ser.write(s.encode("ascii"))
        self.ser.flush()

    def send_command(self, cmd):
        line = cmd.strip() + "\n"
        self._write_raw(line)

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

    def get_current_z(self):
        self._write_raw("?")
        start = time.time()
        while time.time() - start < 1.0:
            if self.ser.in_waiting:
                line = self.ser.readline().decode("ascii", errors="ignore").strip()
                if "WPos:" in line:
                    s = line.split("WPos:")[1].split("|")[0]
                    z = float(s.split(",")[2])
                    return z
                if "MPos:" in line:
                    s = line.split("MPos:")[1].split("|")[0]
                    z = float(s.split(",")[2])
                    return z
            else:
                time.sleep(0.01)
        raise TimeoutError("Could not read Z position")

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()


def main():
    gc = GrblController(GRBL_PORT, GRBL_BAUD)

    pygame.init()
    pygame.joystick.init()

    if pygame.joystick.get_count() == 0:
        print("No controller detected.")
        return

    js = pygame.joystick.Joystick(0)
    js.init()

    print("Controls:")
    print("  A (0) → +Z jog   (incremental, $J=G91)")
    print("  B (1) → -Z jog   (incremental, $J=G91)")
    print("  X (2) → Save Z as HOME (absolute position)")
    print("  Y (3) → Go to HOME Z   (G90 G0 Z...)")
    print("Press Ctrl+C to quit.\n")

    home_z = None
    prev_x = 0
    prev_y = 0
    last_cmd_time = 0.0

    try:
        while True:
            pygame.event.pump()

            a_pressed = js.get_button(0)
            b_pressed = js.get_button(1)
            x_pressed = js.get_button(2)
            y_pressed = js.get_button(3)

            now = time.time()

            # ---- Save HOME Z ----
            if x_pressed and not prev_x:
                try:
                    home_z = gc.get_current_z()
                    print(f"[HOME SET] Z = {home_z:.3f}")
                except Exception as e:
                    print("Failed to read Z:", e)

            # ---- Return to HOME Z (absolute, G90 G0 JUST Z) ----
            if y_pressed and not prev_y:
                if home_z is None:
                    print("No HOME set yet.")
                else:
                    try:
                        # Absolute rapid move to saved Z
                        gcode = f"G90 G0 Z{home_z:.3f}  ; move to home Z (absolute)"
                        # If you ever want to go back to jog-based home, use this instead:
                        # current_z = gc.get_current_z()
                        # dz = home_z - current_z
                        # gcode = f\"$J=G91 Z{dz:.3f} F{FEED_MM_PER_MIN:.1f}\"

                        print("[HOME GO] Sending:", gcode)
                        resp = gc.send_command(gcode)
                        print("  GRBL:", resp)
                        last_cmd_time = time.time()

                    except Exception as e:
                        print("Failed to move home:", e)

            prev_x = x_pressed
            prev_y = y_pressed

            # ---- Manual Z Jog (still incremental) ----
            if now - last_cmd_time >= COMMAND_INTERVAL:
                dz = 0.0
                if a_pressed:
                    dz = MAX_STEP_MM
                elif b_pressed:
                    dz = -MAX_STEP_MM

                if dz != 0.0:
                    gcode = f"$J=G91 Z{dz:.3f} F{FEED_MM_PER_MIN:.1f}"
                    print("Sending:", gcode)
                    resp = gc.send_command(gcode)
                    print("  GRBL:", resp)
                    last_cmd_time = now

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nExiting...")

    finally:
        gc.close()
        pygame.quit()


if __name__ == "__main__":
    main()