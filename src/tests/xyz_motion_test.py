import time
import pygame
import serial


# ============================================================
# Comprehensive GRBL + Xbox button-control motion test
#
# Purpose:
#   - Test X/Y/Z motion from one file
#   - Use ABXY + bumper buttons for fixed-step motion
#   - Save a HOME position
#   - Return to HOME position
#
# Control mapping:
#   X button -> -X by 5 mm
#   B button -> +X by 5 mm
#   Y button -> +Y by 5 mm
#   A button -> -Y by 5 mm
#   LB       -> +Z by 5 mm
#   RB       -> -Z by 5 mm
#   LT       -> Save current XYZ as HOME
#   RT       -> Move back to saved HOME
#
# Notes:
#   - Each button press = exactly one step
#   - Holding a button does NOT repeat motion
#   - HOME move uses absolute G90 motion
# ============================================================


# ---------- USER CONFIG ----------
GRBL_PORT = "/dev/ttyACM0"
GRBL_BAUD = 115200

STEP_MM_XY = 5.0
STEP_MM_Z = 5.0

FEED_XY_MM_PER_MIN = 1200.0
FEED_Z_MM_PER_MIN = 500.0
HOME_FEED_MM_PER_MIN = 800.0

STATUS_INTERVAL = 0.25
TRIGGER_THRESHOLD = 0.85
# ---------------------------------


class GrblController:
    def __init__(self, port: str, baudrate: int = 115200):
        self.port = port
        self.baudrate = baudrate
        self.ser = None

    def connect(self):
        print(f"Opening GRBL serial port {self.port} @ {self.baudrate}...")
        self.ser = serial.Serial(self.port, self.baudrate, timeout=1, write_timeout=1)

        time.sleep(2.0)
        self.ser.reset_input_buffer()

        self._write_raw("\r\n\r\n")
        time.sleep(2.0)
        self.ser.reset_input_buffer()

        unlock_resp = self.send_command("$X")
        print("Unlock response:", unlock_resp)

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

        coords = None
        if "WPos:" in line:
            coords = line.split("WPos:")[1].split("|")[0]
        elif "MPos:" in line:
            coords = line.split("MPos:")[1].split("|")[0]

        if not coords:
            return None

        try:
            x_str, y_str, z_str = coords.split(",")
            return float(x_str), float(y_str), float(z_str)
        except Exception:
            return None

    def get_machine_state(self):
        line = self.query_status_line()
        if not line:
            return None
        try:
            return line.split("|")[0].strip("<>")
        except Exception:
            return None
    def jog(self, dx=0.0, dy=0.0, dz=0.0):
        parts = ["G91"]  # incremental mode

        move = ["G0"]  # rapid move

        if dx != 0.0:
            move.append(f"X{dx:.3f}")
        if dy != 0.0:
            move.append(f"Y{dy:.3f}")
        if dz != 0.0:
            move.append(f"Z{dz:.3f}")

        command = " ".join(parts + move)
        return self.send_command(command)

    def move_to(self, x=None, y=None, z=None, feed=800.0):
        parts = ["G90", "G0"]
        if x is not None:
            parts.append(f"X{x:.3f}")
        if y is not None:
            parts.append(f"Y{y:.3f}")
        if z is not None:
            parts.append(f"Z{z:.3f}")
        if feed is not None:
            parts.append(f"F{feed:.1f}")
        return self.send_command(" ".join(parts))

    def feed_hold(self):
        if self.ser:
            self.ser.write(b"!")
            self.ser.flush()

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            self.ser = None


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def normalize_trigger(raw: float) -> float:
    # Common Xbox trigger mapping: -1 released, +1 fully pressed
    return clamp((raw + 1.0) / 2.0, 0.0, 1.0)


def main():
    gc = GrblController(GRBL_PORT, GRBL_BAUD)

    try:
        gc.connect()
    except Exception as e:
        print("Failed to connect to GRBL:", e)
        return

    pygame.init()
    pygame.joystick.init()

    if pygame.joystick.get_count() == 0:
        print("No controller detected.")
        gc.close()
        return

    js = pygame.joystick.Joystick(0)
    js.init()

    has_hat = js.get_numhats() > 0

    print(f"Connected controller: {js.get_name()}")
    print()
    print("Control mapping:")
    print("  D-pad Left/Right -> X motion")
    print("  D-pad Up/Down    -> Y motion")
    print("  LB -> +Z by 5 mm")
    print("  RB -> -Z by 5 mm")
    print("  LT -> Save current XYZ as HOME")
    print("  RT -> Move to saved HOME")
    print("  Ctrl+C -> quit")
    print()

    home_xyz = None
    last_status_time = 0.0
    last_move_time = 0.0
    MOVE_INTERVAL = 0.05

    # Button mapping (typical Xbox)
    LB_BTN = 4
    RB_BTN = 5

    prev_lb = False
    prev_rb = False
    prev_lt_active = False
    prev_rt_active = False

    axes_count = js.get_numaxes()
    print(f"Detected {axes_count} axes")
    print(f"Detected {js.get_numhats()} hat(s)")
    print()

    try:
        while True:
            pygame.event.pump()

            lb_pressed = bool(js.get_button(LB_BTN))
            rb_pressed = bool(js.get_button(RB_BTN))

            lt_raw = js.get_axis(2) if axes_count > 2 else -1.0
            rt_raw = js.get_axis(5) if axes_count > 5 else -1.0

            lt = normalize_trigger(lt_raw)
            rt = normalize_trigger(rt_raw)

            lt_active = lt >= TRIGGER_THRESHOLD
            rt_active = rt >= TRIGGER_THRESHOLD

            hat_x, hat_y = js.get_hat(0) if has_hat else (0, 0)

            # ---------- HOME ----------
            if lt_active and not prev_lt_active:
                pos = gc.get_position()
                if pos is not None:
                    home_xyz = pos
                    print(f"[HOME SET] X={home_xyz[0]:.3f} Y={home_xyz[1]:.3f} Z={home_xyz[2]:.3f}")
                else:
                    print("[HOME SET] Failed to read current position")

            if rt_active and not prev_rt_active:
                if home_xyz is None:
                    print("[HOME GO] No HOME saved yet")
                else:
                    print(
                        f"[HOME GO] Moving to X={home_xyz[0]:.3f} "
                        f"Y={home_xyz[1]:.3f} Z={home_xyz[2]:.3f}"
                    )
                    resp = gc.move_to(
                        x=home_xyz[0],
                        y=home_xyz[1],
                        z=home_xyz[2],
                        feed=HOME_FEED_MM_PER_MIN,
                    )
                    print("  GRBL:", resp)

            prev_lt_active = lt_active
            prev_rt_active = rt_active

            # ---------- D-PAD CONTINUOUS XY ----------
            dx = 0.0
            dy = 0.0

            if hat_x == -1:
                dx = -STEP_MM_XY
            elif hat_x == 1:
                dx = STEP_MM_XY

            if hat_y == 1:
                dy = STEP_MM_XY
            elif hat_y == -1:
                dy = -STEP_MM_XY

            now = time.time()
            if (dx != 0.0 or dy != 0.0) and (now - last_move_time >= MOVE_INTERVAL):
                resp = gc.jog(dx=dx, dy=dy, dz=0.0)
                print(f"[JOG] dx={dx:.1f} dy={dy:.1f} ->", resp)
                last_move_time = now

            # ---------- Z fixed-step motion ----------
            if lb_pressed and not prev_lb:
                resp = gc.jog(dx=0.0, dy=0.0, dz=+STEP_MM_Z)
                print("[STEP] +Z 5 mm ->", resp)

            if rb_pressed and not prev_rb:
                resp = gc.jog(dx=0.0, dy=0.0, dz=-STEP_MM_Z)
                print("[STEP] -Z 5 mm ->", resp)

            prev_lb = lb_pressed
            prev_rb = rb_pressed

            # ---------- Status ----------
            if now - last_status_time >= STATUS_INTERVAL:
                pos = gc.get_position()
                state = gc.get_machine_state()

                if pos is not None:
                    print(
                        f"[STATUS] State={state or 'Unknown':>6} | "
                        f"X={pos[0]:8.3f} Y={pos[1]:8.3f} Z={pos[2]:8.3f}"
                    )
                else:
                    print(f"[STATUS] State={state or 'Unknown':>6} | Position unavailable")

                last_status_time = now

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\\nExiting motion test...")

    finally:
        try:
            gc.feed_hold()
        except Exception:
            pass

        gc.close()
        try:
            js.quit()
            pygame.joystick.quit()
            pygame.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()