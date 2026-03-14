import socket
import subprocess
import numpy as np
import cv2
import time

# --- CONFIGURATION ---
RASPI_IP = "172.20.10.14"  # Update to your actual Pi IP
HELLO_PORT = 7123
WIDTH = 640
HEIGHT = 480

def trigger_stream():
    print(f"Sending HELLO to Pi at {RASPI_IP}...")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(b"HELLO", (RASPI_IP, HELLO_PORT))
    time.sleep(1.0)

def receive_stream():
    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "quiet",
        "-protocol_whitelist", "file,udp,rtp",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-i", f"udp://0.0.0.0:5005", # Make sure this matches your VIDEO_PORT
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-"
    ]

    print("Receiving stream... Press ESC to quit.")
    proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, bufsize=10**6)

    try:
        while True:
            raw_frame = proc.stdout.read(WIDTH * HEIGHT * 3)
            if not raw_frame or len(raw_frame) != WIDTH * HEIGHT * 3:
                break # Exit if the pipe breaks or data ends

            frame = np.frombuffer(raw_frame, np.uint8).reshape((HEIGHT, WIDTH, 3))
            cv2.imshow("Toolhead Camera", frame)

            if cv2.waitKey(1) == 27:
                break
    except Exception as e:
        print(f"Stream interrupted: {e}")
    finally:
        proc.terminate()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    trigger_stream()
    receive_stream()