#include <EEPROM.h>

#define EEPROM_SIZE 132

void setup() {
  Serial.begin(115200);
  EEPROM.begin(EEPROM_SIZE);

  Serial.println("Erasing EEPROM...");
  for (int i = 0; i < EEPROM_SIZE; i++)
    EEPROM.write(i, 0);
  EEPROM.commit();

  Serial.println("Done! EEPROM cleared.");
  Serial.println("Now upload Esp_TruckSetup code and reboot.");
}

void loop() {}
