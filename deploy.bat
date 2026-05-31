@ECHO OFF
REM ─────────────────────────────────────────────────────────────────────────────
REM deploy.bat  –  Upload app files to the ESP32 and reboot.
REM
REM Usage:
REM   deploy.bat          Full deploy: all app/ files + boot.py + main.py
REM   deploy.bat quick    Quick deploy: website_helpers.py + website.py only
REM
REM IMPORTANT: disconnect the Jupyter kernel from the COM port first.
REM            (Kernel > Disconnect in the notebook, or close the kernel.)
REM
REM The COM port is set in deploy.py (PORT = 'COM8').
REM ─────────────────────────────────────────────────────────────────────────────

python deploy.py %1
