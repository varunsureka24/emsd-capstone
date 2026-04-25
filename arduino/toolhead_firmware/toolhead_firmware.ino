// Toolhead firmware: reads HX711 load cell, streams kg readings to Pi,
// and fires the spot weld relay on command from the Pi.
//
// Library: "HX711" by bogde — install via Arduino Library Manager
//
// Wiring (matches your working test sketch):
//   HX711 DOUT  →  Arduino D3
//   HX711 CLK   →  Arduino D2
//   Relay signal →  Arduino D7
//   HX711 VCC / relay VCC  →  5V
//   GND → GND
//
// ---- Calibration ----
// Set calibration_factor by running SparkFun's calibration sketch until
// scale.get_units() matches a known weight. Your current value (-7050)
// is a good starting point; fine-tune if readings drift.
//
// Serial protocol (to Pi, 115200 baud):
//   "READY\n"        — once on startup after tare
//   "FSR:<kg>\n"     — force reading at SAMPLE_HZ (e.g. "FSR:1.23")
//
// Serial protocol (from Pi):
//   "TARE\n"         — re-zero with probe in air
//   "WELD_ON\n"      — close relay (Pi controls dwell duration)
//   "WELD_OFF\n"     — open relay

#include "HX711.h"

#define DOUT_PIN        3
#define CLK_PIN         2
#define RELAY_PIN       7
#define SAMPLE_HZ       20       // readings per second sent to Pi
#define AVG_SAMPLES     3        // average N readings to reduce noise (cuts ~0.6 kg jitter to ~0.2 kg)

float calibration_factor = -7050;  // tune with SparkFun calibration sketch

HX711 scale;
unsigned long lastSend = 0;

void setup() {
    Serial.begin(115200);

    pinMode(RELAY_PIN, OUTPUT);
    digitalWrite(RELAY_PIN, LOW);

    scale.begin(DOUT_PIN, CLK_PIN);
    scale.set_scale(calibration_factor);
    scale.tare();

    Serial.println("READY");
}

void loop() {
    // Handle commands from Pi
    if (Serial.available()) {
        String cmd = Serial.readStringUntil('\n');
        cmd.trim();
        if (cmd.equalsIgnoreCase("TARE")) {
            scale.tare();
            Serial.println("TARED");
        } else if (cmd.equalsIgnoreCase("WELD_ON")) {
            digitalWrite(RELAY_PIN, HIGH);
        } else if (cmd.equalsIgnoreCase("WELD_OFF")) {
            digitalWrite(RELAY_PIN, LOW);
        }
    }

    // Stream force readings to Pi
    unsigned long now = millis();
    if (now - lastSend >= (1000UL / SAMPLE_HZ)) {
        lastSend = now;
        if (scale.is_ready()) {
            float kg = scale.get_units(AVG_SAMPLES) / 2.2f;
            Serial.print("FSR:");
            Serial.println(kg, 2);  // 2 decimal places, e.g. "FSR:1.23"
        }
    }
}
