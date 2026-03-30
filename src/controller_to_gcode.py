import pygame
import time

# Max step per command in mm
MAX_STEP_MM = 1.0

# Feed rate for jogs in mm/min
FEED_MM_PER_MIN = 500.0

# How often to emit a jog command (seconds)
COMMAND_INTERVAL = 0.2


def main():
    pygame.init()
    pygame.joystick.init()

    if pygame.joystick.get_count() == 0:
        print("No controller detected. Plug in your Xbox controller.")
        return

    js = pygame.joystick.Joystick(0)
    js.init()
    print(f"Controller connected: {js.get_name()}")
    print("D-Pad to generate X/Y jogging G-code:")
    print("  - Left/right → X- / X+")
    print("  - Up/down    → Y+ / Y-")
    print("Buttons to generate Z jogging G-code:")
    print("  - A (0) → Z+")
    print("  - B (1) → Z-")
    print("Press Ctrl+C to quit.\n")

    last_cmd_time = 0.0

    try:
        while True:
            # Let pygame process internal events
            pygame.event.pump()

            # D-pad is usually hat 0 on Xbox-style controllers
            hat_x, hat_y = js.get_hat(0)  # each in {-1, 0, 1}

            # Buttons for Z
            a_pressed = js.get_button(0)  # A
            b_pressed = js.get_button(1)  # B

            now = time.time()
            if now - last_cmd_time >= COMMAND_INTERVAL:
                # X/Y from D-pad (discrete steps)
                dx = MAX_STEP_MM * hat_x        # -1, 0, +1 → -step, 0, +step
                dy = MAX_STEP_MM * hat_y        # up: +1 → +Y, down: -1 → -Y

                # Z from buttons
                dz = 0.0
                if a_pressed:
                    dz += MAX_STEP_MM   # Z+
                if b_pressed:
                    dz -= MAX_STEP_MM   # Z-

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
                    print(gcode)

                    last_cmd_time = now

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nExiting...")

    finally:
        pygame.joystick.quit()
        pygame.quit()


if __name__ == "__main__":
    main()