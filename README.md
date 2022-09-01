Activities:


Base:
- Setup source control with git - DONE /nalkema1/MotionSprinkler
- Create motion sensor module in Python - CODED  - TESTED
- Create test harnass - CODED
- Create sprinker activation module in Python - CODED
- Create sprinker activation logic - CODED
- Power ESP32 by converting 9v to 5.1v - DONE
- Solder all components together and finalize enclosure box - DONE
- enable USB access to EPS32 to support direct loading of software - DONE 

Base Plus:
- Add Wifi manager -DONE
- Add OTA module and get new versions from GIT (or - enable USB access to EPS32 to support direct loading of software ) - DONE
- sync with NTP to get the actual time/date - DONE
- Logging

Future:
- Take picture when motion is activated
- Battery operated system instead of wired
- Custom printed enclosure for electronics and solenoid
- Enable custom schedule for regular watering, pull schedule from website
- Check external website for custom instructions, e.g. force new version, pause, reboot
- Detect scenario when application crashed, and force reboot, upload of logfiles
- Enable system to activate even when there is no wifi, or provide button to activate wifisetup


Installation Instructions:

PIR Config:
- 3.3v to 3.3v on ESP32
- GRD to GRD on ESP32
- Data to GPIO 14 on ESP32


Libraries needed for pico webserver:
import upip
upip.install('picoweb')
upip.install("micropython-ulogging")


