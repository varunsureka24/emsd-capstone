import socket
import subprocess

# --- CONFIGURATION ---
UDP_PORT = 7123
VIDEO_PORT = 5005
CAMERA_NODE = "/dev/video0"

def wait_for_client():
    print(f"Listening for HELLO on UDP {UDP_PORT}...")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("", UDP_PORT))
        data, client_addr = sock.recvfrom(1024)
        print(f"Client connected from: {client_addr[0]}")
        return client_addr[0]

def start_stream(client_ip):
    cmd = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "error",
        "-f", "v4l2",
        "-input_format", "mjpeg",
        "-video_size", "640x480",
        "-framerate", "30",
        "-i", CAMERA_NODE,
        "-vcodec", "copy",        # Just copy the MJPEG frames directly
        "-f", "avi",              # AVI is better for raw MJPEG over UDP
        f"udp://{client_ip}:{VIDEO_PORT}?pkt_size=1024&buffer_size=65535"
    ]
    
    print(f"Streaming MJPEG to {client_ip}:{VIDEO_PORT}...")
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\nStopping stream.")

if __name__ == "__main__":
    target_ip = wait_for_client()
    start_stream(target_ip)