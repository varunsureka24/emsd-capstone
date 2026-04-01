import serial
import time

PORT = "/dev/ttyACM0"  # change if needed
BAUD = 115200

def main():
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1)
        time.sleep(2.0)
        ser.reset_input_buffer()
    except Exception as e:
        print("Failed to open serial port:", e)
        return

    print(f"Reading force sensor from {PORT} @ {BAUD}...\n")

    min_val = None
    max_val = None
    values = []

    while True:
        try:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue

            # Handle optional "FSR:xxx" format
            if line.startswith("FSR:"):
                line = line.split(":", 1)[1]

            try:
                val = int(line)
            except ValueError:
                continue

            values.append(val)
            if len(values) > 100:
                values.pop(0)

            # Track global min/max
            if min_val is None or val < min_val:
                min_val = val
            if max_val is None or val > max_val:
                max_val = val

            avg = sum(values) / len(values)

            print(
                f"\rCurrent: {val:4d} | "
                f"Min: {min_val:4d} | "
                f"Max: {max_val:4d} | "
                f"Avg: {avg:6.2f}",
                end=""
            )

        except KeyboardInterrupt:
            break

    print("\nExiting...")
    ser.close()


if __name__ == "__main__":
    main()