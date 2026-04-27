import time
from turtle import delay


from grbl_controller import GrblController
from force_sensor import ForceSensorReader




GRBL_PORT = "COM3"
FORCE_PORT = "COM5"
GRBL_BAUD = 115200
FORCE_BAUD = 9600


CONTACT_THRESHOLD = 1.25      # kg
HARD_LIMIT = 8.0             # kg
DEBOUNCE_COUNT = 2


Z_TOUCH_STEP = 0.2           # mm per downward jog
Z_TOUCH_FEED = 1000          # mm/min
Z_MAX_DESCENT = 20.0          # mm


TRAVEL_HEIGHT = 5.0         # mm, safe retract height
WELD_TIME = 0.100            # 100 ms




def wait_until_idle(grbl):
    while True:
        state = grbl.get_machine_state()
        if state == "Idle":
            return
        time.sleep(0.05)




def send_weld(force_sensor, on):
    cmd = "WELD_ON" if on else "WELD_OFF"
    force_sensor.send_command(cmd)




def main():
    grbl = GrblController(GRBL_PORT, GRBL_BAUD)
    force = ForceSensorReader(FORCE_PORT, FORCE_BAUD)


    try:
        print("Connecting to GRBL...")
        grbl.connect()


        print("Connecting to force sensor / relay Arduino...")
        force.connect()

        start_pos = grbl.get_position()
        if start_pos is None:
            raise RuntimeError("Could not read GRBL position")


        start_z = start_pos[2]
        print(f"Starting Z: {start_z:.2f}")


        contact_count = 0


        print("Lowering until force is detected...")


        while True:
            force.read_latest()
            val = force.latest_value
            print(val)


            pos = grbl.get_position()
            if pos is not None:
                current_z = pos[2]
            else:
                current_z = start_z


            if val is not None:
                #print(f"Force: {val:.2f} kg | Z: {current_z:.2f}")


                if abs(val) >= HARD_LIMIT:
                    grbl.feed_hold()
                    send_weld(force, False)
                    raise RuntimeError("Hard force limit hit. Stopping.")


                if abs(val) >= CONTACT_THRESHOLD:
                    contact_count += 1
                    time.sleep(1.0)
                else:
                    contact_count = 0


                if contact_count >= DEBOUNCE_COUNT:
                    print("Contact detected.")
                    grbl.jog_cancel()
                    time.sleep(0.2)
                    break


            if abs(current_z - start_z) >= Z_MAX_DESCENT:
                grbl.jog_cancel()
                send_weld(force, False)
                raise RuntimeError("No contact detected before max descent.")


            grbl.jog(dz=-Z_TOUCH_STEP, feed=Z_TOUCH_FEED)
            time.sleep(0.12)


        print("Relay ON for 100 ms...")
        send_weld(force, True)
        time.sleep(WELD_TIME)
        send_weld(force, False)
        print("Relay OFF.")


        print(f"Raising Z to travel height: {TRAVEL_HEIGHT:.2f}")
        grbl.move_to(z=TRAVEL_HEIGHT, feed=Z_TOUCH_FEED)
        wait_until_idle(grbl)


        print("Done.")


    except KeyboardInterrupt:
        print("\nInterrupted. Turning relay off and stopping jog.")
        send_weld(force, False)
        grbl.jog_cancel()


    finally:
        try:
            send_weld(force, False)
        except Exception:
            pass
        force.close()
        grbl.close()




if __name__ == "__main__":
    main()

