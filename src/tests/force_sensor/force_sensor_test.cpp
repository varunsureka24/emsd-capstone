const int FORCE_PIN = A0;

void setup() {
  Serial.begin(115200);
}

void loop() {
  int value = analogRead(FORCE_PIN);
  Serial.println(value);
  delay(20);
}