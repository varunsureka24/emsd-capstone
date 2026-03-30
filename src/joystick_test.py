import pygame
import sys
import time

pygame.init()
pygame.joystick.init()

if pygame.joystick.get_count() == 0:
    print("No controller detected.")
    sys.exit(1)

js = pygame.joystick.Joystick(0)
js.init()

print(f"✅ Connected to: {js.get_name()}")
print(f"   Axes: {js.get_numaxes()}, Buttons: {js.get_numbuttons()}, Hats: {js.get_numhats()}")
print("Move sticks / press buttons. Press Ctrl+C to quit.\n")

last_print = 0

try:
    while True:
        # Process internal pygame events
        pygame.event.pump()

        # Read axes, buttons, and d-pad (hat)
        axes = [round(js.get_axis(i), 2) for i in range(js.get_numaxes())]
        buttons = [js.get_button(i) for i in range(js.get_numbuttons())]
        hats = [js.get_hat(i) for i in range(js.get_numhats())]

        # Throttle output to ~10 Hz so it’s readable
        now = time.time()
        if now - last_print > 0.1:
            last_print = now
            # \r + end="" keeps it on (mostly) one line in the terminal
            print(f"\rAxes: {axes}  Buttons: {buttons}  Hats: {hats}", end="")

        time.sleep(0.01)

# lx, ly = js.get_axis(0), js.get_axis(1)  # left stick
# rx, ry = js.get_axis(2), js.get_axis(3)  # right stick
# a_button = js.get_button(0)              # A button

# # Example: map left stick Y to robot forward/back
# velocity = -ly  # invert if needed

except KeyboardInterrupt:
    print("\nExiting...")
finally:
    js.quit()
    pygame.joystick.quit()
    pygame.quit()