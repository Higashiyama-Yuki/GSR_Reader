/*
 * GSR Dual Sensor - Serial Data Sender
 * Board: Seeedstudio XIAO ESP32C3
 * Sensors: 2x Grove GSR on A0, A1
 *
 * Output format (CSV): timestamp_ms,gsr1_raw,gsr2_raw
 * Sampling: 100 Hz (configurable via SAMPLE_RATE_HZ build flag)
 */

#include <Arduino.h>

// Pin definitions (overridable via build_flags)
#ifndef GSR_PIN_1
#define GSR_PIN_1 A0
#endif

#ifndef GSR_PIN_2
#define GSR_PIN_2 A1
#endif

#ifndef SAMPLE_RATE_HZ
#define SAMPLE_RATE_HZ 100
#endif

static const unsigned long SAMPLE_INTERVAL_US = 1000000UL / SAMPLE_RATE_HZ;

// Running average for baseline display (optional serial feedback)
static long gsr1_sum = 0;
static long gsr2_sum = 0;
static int  sample_count = 0;

void setup() {
    Serial.begin(115200);

    // 12-bit ADC resolution (0-4095)
    analogReadResolution(12);

    // Let ADC settle
    delay(500);

    // Discard first few readings for sensor warm-up
    for (int i = 0; i < 50; i++) {
        analogRead(GSR_PIN_1);
        analogRead(GSR_PIN_2);
        delay(10);
    }

    // Header line (helps PC-side parser identify start of stream)
    Serial.println("# GSR Dual Sensor Stream");
    Serial.println("# Format: timestamp_ms,gsr1,gsr2");
    Serial.println("# START");
}

void loop() {
    static unsigned long last_sample_us = 0;
    unsigned long now_us = micros();

    if (now_us - last_sample_us >= SAMPLE_INTERVAL_US) {
        last_sample_us += SAMPLE_INTERVAL_US;

        // Read both sensors back-to-back (< 200μs total)
        int gsr1 = analogRead(GSR_PIN_1);
        int gsr2 = analogRead(GSR_PIN_2);

        unsigned long ts = millis();

        // CSV output
        Serial.print(ts);
        Serial.print(',');
        Serial.print(gsr1);
        Serial.print(',');
        Serial.println(gsr2);
    }
}
