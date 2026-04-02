import cv2

cap = cv2.VideoCapture(0, cv2.CAP_V4L2)

if not cap.isOpened():
    print("Camera failed to open")
    exit()

print("Camera opened")

while True:
    ret, frame = cap.read()

    if not ret:
        print("No frame")
        break

    cv2.imshow("Camera Test", frame)

    if cv2.waitKey(1) == 27:  # ESC
        break

cap.release()
cv2.destroyAllWindows()