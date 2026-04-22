import pygame


class ControllerInput:
    def __init__(self):
        self.joystick = None

    def connect(self):
        pygame.init()
        pygame.joystick.init()

        if pygame.joystick.get_count() == 0:
            raise RuntimeError("No controller detected")

        self.joystick = pygame.joystick.Joystick(0)
        self.joystick.init()

    def get_name(self):
        if self.joystick is None:
            return "No controller"
        return self.joystick.get_name()

    STICK_DEADZONE = 0.25
    # Minor axis must be at least this fraction of the major axis to allow diagonal movement.
    # 0.41 ≈ tan(22.5°), meaning the stick must be within 22.5° of a diagonal to move diagonally;
    # anything closer to a cardinal axis snaps to pure X or Y.
    DIAGONAL_THRESHOLD = 0.41

    def poll(self):
        if self.joystick is None:
            return {
                "left": False,
                "right": False,
                "up": False,
                "down": False,
                "hat_x": 0,
                "hat_y": 0,
                "stick_x": 0.0,
                "stick_y": 0.0,
            }

        pygame.event.pump()
        hat_x, hat_y = self.joystick.get_hat(0)

        raw_x = self.joystick.get_axis(0)
        raw_y = self.joystick.get_axis(1)
        stick_x = raw_x if abs(raw_x) >= self.STICK_DEADZONE else 0.0
        stick_y = -raw_y if abs(raw_y) >= self.STICK_DEADZONE else 0.0  # invert: up = positive

        # Snap to cardinal: zero out the minor axis unless the stick is pushed
        # close enough to a true diagonal.
        if stick_x != 0.0 and stick_y != 0.0:
            if abs(stick_y) < abs(stick_x) * self.DIAGONAL_THRESHOLD:
                stick_y = 0.0
            elif abs(stick_x) < abs(stick_y) * self.DIAGONAL_THRESHOLD:
                stick_x = 0.0

        return {
            "left": hat_x < 0,
            "right": hat_x > 0,
            "up": hat_y > 0,
            "down": hat_y < 0,
            "hat_x": hat_x,
            "hat_y": hat_y,
            "stick_x": stick_x,
            "stick_y": stick_y,
        }

    def close(self):
        try:
            if self.joystick is not None:
                self.joystick.quit()
                self.joystick = None
            pygame.joystick.quit()
            pygame.quit()
        except Exception:
            pass