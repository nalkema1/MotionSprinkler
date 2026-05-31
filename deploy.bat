@ECHO OFF
REM ─────────────────────────────────────────────────────────────────────────────
REM deploy.bat  –  Upload app files to the ESP32 via ampy and reboot.
REM
REM Usage:
REM   deploy.bat          Full deploy: all app/ files + boot.py + main.py
REM   deploy.bat quick    Quick deploy: only app/website.py  (fastest for UI work)
REM
REM Set PORT below to match your current COM port.
REM ─────────────────────────────────────────────────────────────────────────────

SET PORT=COM8
SET BAUD=115200
SET AMPY=ampy --port %PORT% --baud %BAUD%

REM ── Ensure ampy is installed ──────────────────────────────────────────────────
ampy --version >NUL 2>&1
IF ERRORLEVEL 1 (
    ECHO ampy not found - installing adafruit-ampy via pip ...
    pip install adafruit-ampy
    IF ERRORLEVEL 1 ( ECHO ERROR: pip install failed. Run: pip install adafruit-ampy & EXIT /B 1 )
    ECHO.
)

ECHO.
ECHO MotionSprinkler deploy  ^|  port=%PORT%
ECHO ─────────────────────────────────────────

REM ── Quick deploy ──────────────────────────────────────────────────────────────
IF /I "%1"=="quick" (
    ECHO [quick] Uploading app\website.py ...
    %AMPY% put app/website.py app/website.py
    IF ERRORLEVEL 1 ( ECHO ERROR: upload failed & EXIT /B 1 )
    ECHO [quick] Resetting device ...
    %AMPY% reset
    ECHO Done.
    EXIT /B 0
)

REM ── Full deploy ───────────────────────────────────────────────────────────────
ECHO [full] Creating /app directory on device (safe to ignore "exists" error)
%AMPY% mkdir app 2>NUL

ECHO [full] Uploading app files ...
FOR %%F IN (
    app\httpclient.py
    app\logging.py
    app\motiondetect.py
    app\ota_updater.py
    app\start.py
    app\telemetry.py
    app\timesync.py
    app\website.py
    app\wifi_manager.py
) DO (
    ECHO   ^> %%F
    %AMPY% put %%F %%F
    IF ERRORLEVEL 1 ( ECHO ERROR uploading %%F & EXIT /B 1 )
)

ECHO [full] Uploading root files ...
FOR %%F IN (boot.py main.py) DO (
    ECHO   ^> %%F
    %AMPY% put %%F %%F
    IF ERRORLEVEL 1 ( ECHO ERROR uploading %%F & EXIT /B 1 )
)

ECHO [full] Resetting device ...
%AMPY% reset

ECHO.
ECHO Deploy complete. The device is rebooting.
ECHO On first boot it will auto-create device_settings.json with default GPIO pins:
ECHO   Zone 1 = GPIO 17,  Zone 2 = GPIO 19,  Zone 3 = GPIO 20,  Zone 4 = GPIO 21
ECHO Visit http://<device-ip>/ to confirm the new UI is running.
