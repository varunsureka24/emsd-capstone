#include "HX711.h"

#define DOUT  3
#define CLK   2
#define RELAY_PIN 7

#define AVG_SAMPLES 1
#define WELD_PULSE_MS 100

float calibration_factor = -7050;

HX711 scale;
bool relayOn = false;
unsigned long relayStart = 0;
unsigned long lastSend = 0;

void setup() {
    Serial.begin(9600);

    pinMode(RELAY_PIN, OUTPUT);
    digitalWrite(RELAY_PIN, LOW);

    scale.begin(DOUT, CLK);
    scale.set_scale(calibration_factor);
    scale.tare();

    Serial.println("READY");
}

void loop() {
    // Handle relay pulse timing
    if (relayOn && (millis() - relayStart >= WELD_PULSE_MS)) {
        digitalWrite(RELAY_PIN, LOW);
        relayOn = false;
    }

    // Handle commands from Pi
    if (Serial.available()) {
        String cmd = Serial.readStringUntil('\n');
        cmd.trim();
        if (cmd.equalsIgnoreCase("WELD_ON") && !relayOn) {
            digitalWrite(RELAY_PIN, HIGH);
            relayOn = true;
            relayStart = millis();
        } else if (cmd.equalsIgnoreCase("TARE")) {
            scale.tare();
            Serial.println("TARED");
        }
    }

    // Stream force readings to Pi at ~10 Hz (matches HX711 hardware output rate)
    if (millis() - lastSend >= 100) {
        lastSend = millis();
        if (scale.is_ready()) {
            float kg = scale.get_units(AVG_SAMPLES) / 2.2f;
            Serial.println(kg, 2);
        }
    }
}
