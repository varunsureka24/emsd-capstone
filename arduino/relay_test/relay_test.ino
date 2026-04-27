#define RELAY_PIN 7

void setup() {
    Serial.begin(115200);
    pinMode(RELAY_PIN, OUTPUT);
    digitalWrite(RELAY_PIN, LOW);
    Serial.println("Relay test ready. Send 'ON' or 'OFF'.");
}

void loop() {
    if (Serial.available()) {
        String cmd = Serial.readStringUntil('\n');
        cmd.trim();
        if (cmd.equalsIgnoreCase("ON")) {
            digitalWrite(RELAY_PIN, HIGH);
            Serial.println("Relay ON");
        } else if (cmd.equalsIgnoreCase("OFF")) {
            digitalWrite(RELAY_PIN, LOW);
            Serial.println("Relay OFF");
        } else {
            Serial.println("Unknown command. Send 'ON' or 'OFF'.");
        }
    }
}
