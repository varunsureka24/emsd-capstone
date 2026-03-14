import cv2

# Open the default camera (equivalent to /dev/video0)
cap = cv2.VideoCapture(0)

# Set the resolution (optional, matches your previous script)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

if not cap.isOpened():
    print("Error: Could not open the camera.")
    exit()

print("Starting local video stream. Press 'ESC' to exit.")

while True:
    # Capture frame-by-frame directly from the camera
    ret, frame = cap.read()
    
    if not ret:
        print("Error: Failed to grab a frame.")
        break

    # Display the resulting frame on the Pi's monitor
    cv2.imshow("Local Pi Display", frame)

    # Wait 1ms and check if the ESC key (ASCII 27) was pressed
    if cv2.waitKey(1) == 27:
        break

# Release the camera hardware and close the window
cap.release()
cv2.destroyAllWindows()