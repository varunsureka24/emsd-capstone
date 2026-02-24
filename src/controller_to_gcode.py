import pygame
import time

# How "hard" you have to push the stick before it counts
DEADZONE = 0.4

# Max step per command in mm (at full stick deflection)
MAX_STEP_MM = 1.0

# Feed rate for jogs in mm/min
FEED_MM_PER_MIN = 500.0

# How often to emit a jog command (seconds)
COMMAND_INTERVAL = 0.2


def axis_to_step(val: float, max_step: float, deadzone: float) -> float:
    """
    Map joystick axis value (-1..1) to a step size in mm.
    Returns 0 if inside deadzone.
    """
    if abs(val) < deadzone:
        return 0.0
    # scale roughly linearly with stick deflection
    return max_step * val


def main():
    pygame.init()
    pygame.joystick.init()

    if pygame.joystick.get_count() == 0:
        print("No controller detected. Plug in your Xbox controller.")
        return

    js = pygame.joystick.Joystick(0)
    js.init()
    print(f"Controller connected: {js.get_name()}")
    print("Move LEFT STICK to generate X/Y jogging G-code.")
    print("  - Left/right → X- / X+")
    print("  - Up/down    → Y+ / Y-")
    print("Move RT and LT to generate +Z/-Z jogging G-code")
    print("  - RT → Z+")
    print("  - LT → Z- ")
    print("Press Ctrl+C to quit.\n")

    last_cmd_time = 0.0

    try:
        while True:
            # Let pygame process internal events
            pygame.event.pump()

            # Read axes (typical Xbox mapping: 0 = LX, 1 = LY)
            lx = js.get_axis(0)  # left stick X: -1(left) to +1(right)
            ly = js.get_axis(1)  # left stick Y: -1(up)   to +1(down)
            lt = js.get_axis(4)  # Left trigger
            rt = js.get_axis(5)  # Right trigger

            now = time.time()
            if now - last_cmd_time >= COMMAND_INTERVAL:
                dx = axis_to_step(lx, MAX_STEP_MM, DEADZONE)
                dy = axis_to_step(-ly, MAX_STEP_MM, DEADZONE)

                # Triggers sometimes return -1..1 or 0..1 depending on OS
                # Normalize roughly to 0..1
                lt_val = (lt + 1) / 2 if lt < 0 else lt
                rt_val = (rt + 1) / 2 if rt < 0 else rt

                dz = MAX_STEP_MM * (rt_val - lt_val)

                if abs(dz) < DEADZONE:
                    dz = 0.0

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