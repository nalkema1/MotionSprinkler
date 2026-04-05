# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

MicroPython firmware for an ESP32-based motion-activated sprinkler. A PIR sensor on GPIO 14 triggers a relay on GPIO 17. The device connects to WiFi, syncs time via NTP, and pulls OTA updates from this GitHub repo on boot.

**Most `.py` files under `app/` run on the ESP32, not on your PC.** They import MicroPython-only modules (`machine`, `network`, `esp32`, `urequests`, `ubinascii`, `uos`, `ujson`). Do not try to run them with CPython.

## Device boot flow

1. `boot.py` is (intentionally) a no-op.
2. `main.py` calls `app/start.py`.
3. `app/start.py`:
   - Initializes motion sensor (GPIO 14) and relay (GPIO 17).
   - Starts a 60-second `machine.Timer` (`CheckSchedule`) that monitors memory, reconnects WiFi, and force-resets every 24h.
   - Connects WiFi via `WifiManager`, does NTP sync.
   - Runs `OTAUpdater` against `https://github.com/nalkema1/MotionSprinkler`, pulling the `app/` directory; reboots if updated.
   - Imports `app.website` (picoweb server) and enters an empty `while True: pass` loop — the real work happens in the timer callback and PIR IRQ.
4. Telemetry is logged to `telemetry.csv` on the device (rotated at 15 KB) via `app/telemetry.py::sendTelemetry`.

Two toggles at the top of `app/start.py`: `prod`, `bypassupdate`, `alwayscheck_update`. The motion handler is currently wired to `nonaction` (telemetry only) — switch to `myaction` to arm the relay.

## Toolchain

Development is done through Jupyter notebooks running the `jupyter_micropython_kernel` (MicroPython-over-USB). The `.env` venv hosts that kernel; it is **not** for running project code.

- [DirectConnect.ipynb](DirectConnect.ipynb) — interactive REPL session, uploads individual files with `%sendtofile`.
- [initialize.ipynb](initialize.ipynb) — full device provisioning.
- [ampy.ipynb](ampy.ipynb), [2.4 ghz.ipynb](2.4 ghz.ipynb) — ad-hoc device interaction.

`ampy` is also used directly:
```
do.bat <ampy args>        # wraps: ampy --port COM14 --baud 115200 <args>
```
(The COM port is hardcoded in `do.bat` / `esp32/*.bat` — check/update before running. Current board was last seen on COM10.)

### Flashing MicroPython firmware

From [esp32/](esp32/):
```
initialize_esp32.bat      # esptool erase_flash
installfirmware.bat       # esptool write_flash with esp32-20220618-v1.19.1.bin
```

### On-device library install (from a connected REPL)

```python
import upip
upip.install('picoweb')
upip.install('micropython-ulogging')
```

## Hardware wiring

- PIR: 3.3V, GND, data → GPIO 14
- Relay (sprinkler solenoid): GPIO 17
- Power: 9V → 5.1V buck → ESP32

## Repo gotchas

- **The repo lives inside OneDrive.** OneDrive sync locks files during `pip install`, which will cause installs of packages with many small files (especially `jedi`) to fail with `OSError: [Errno 22] Invalid argument`. When installing Python packages into `.env`, use `--no-deps` if deps are already satisfied, or pause OneDrive sync first. The venv being inside OneDrive is the root cause — moving it out (`C:\venvs\...`) is the cleanest fix.
- Files in `app/` are pulled fresh from GitHub by the OTA updater on each boot when `alwayscheck_update = True`. Local edits to the device's `app/` will be overwritten unless you push to master or disable the updater.
- `keys.py` holds an Azure Functions app code used for telemetry posting — it is committed. Treat as low-sensitivity but do not expand its scope.
- `esp32-20220618-v1.19.1.bin` is MicroPython v1.19.1 — old. Anything newer than that may require code adjustments (e.g. `network.WLAN()` constructor changes).
