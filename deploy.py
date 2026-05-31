"""
deploy.py - Upload MotionSprinkler files to ESP32 via serial raw REPL.

Uses the same raw-REPL protocol as the Jupyter micropython kernel,
so no ampy / mpremote dependency is required.

Usage:
    python deploy.py          full deploy (all app files + boot + main)
    python deploy.py quick    quick deploy (website_helpers.py + website.py only)

IMPORTANT: close any Jupyter serial connections to the port before running
(Kernel > Disconnect in the notebook, or Kernel > Shut Down).
"""

import sys
import os
import time

PORT = 'COM8'
BAUD = 115200

FULL_FILES = [
    'app/httpclient.py',
    'app/logging.py',
    'app/motiondetect.py',
    'app/ota_updater.py',
    'app/start.py',
    'app/telemetry.py',
    'app/timesync.py',
    'app/website_helpers.py',
    'app/website.py',
    'app/wifi_manager.py',
    'boot.py',
    'main.py',
]

QUICK_FILES = [
    'app/website_helpers.py',
    'app/website.py',
]

try:
    import serial
except ImportError:
    sys.exit("ERROR: pyserial not installed. Run: pip install pyserial")


class RawREPL:
    def __init__(self, port, baud):
        try:
            self.ser = serial.Serial(port, baud, timeout=1)
        except serial.SerialException as e:
            sys.exit(
                "ERROR: Cannot open {}.\n{}\n"
                "Close the Jupyter kernel first: Kernel > Disconnect".format(port, e)
            )

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def enter(self):
        """Interrupt any running program and enter raw REPL mode."""
        self.ser.write(b'\r\x03\x03')   # Ctrl+C twice
        time.sleep(0.2)
        self.ser.reset_input_buffer()
        self.ser.write(b'\r\x01')        # Ctrl+A = raw REPL
        buf = b''
        deadline = time.time() + 5
        while time.time() < deadline:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n)
                if b'raw REPL' in buf:
                    # drain the rest of the prompt line
                    time.sleep(0.1)
                    self.ser.reset_input_buffer()
                    return
            time.sleep(0.05)
        raise RuntimeError(
            "Could not enter raw REPL. Got: {}\n"
            "Make sure the device is powered and COM port is free.".format(repr(buf[-120:]))
        )

    def exec(self, code):
        """Send one Python statement and wait for the response."""
        if isinstance(code, str):
            code = code.encode()
        # send in <=256-byte chunks with small pauses (matches ampy)
        for i in range(0, len(code), 256):
            self.ser.write(code[i:i + 256])
            time.sleep(0.01)
        self.ser.write(b'\x04')   # Ctrl+D = execute

        # collect until we have OK + two \x04 end-markers
        buf = b''
        deadline = time.time() + 15
        while time.time() < deadline:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n)
            else:
                time.sleep(0.02)
            if b'OK' in buf and buf.count(b'\x04') >= 2:
                break

        if b'OK' not in buf:
            raise RuntimeError("No OK from device. Got: {}".format(repr(buf[-120:])))

        # response format: OK[stdout]\x04[stderr]\x04
        after_ok = buf[buf.index(b'OK') + 2:]
        parts = after_ok.split(b'\x04', 2)
        stderr = parts[1].strip() if len(parts) > 1 else b''
        if stderr:
            raise RuntimeError("Device error: " + stderr.decode(errors='replace').strip())
        return parts[0] if parts else b''

    def upload(self, local_path):
        """Upload a local file to the device, preserving the path."""
        remote = local_path.replace('\\', '/')
        with open(local_path, 'rb') as f:
            data = f.read()
        self.exec("f=open('{}','wb')".format(remote))
        for i in range(0, len(data), 128):
            self.exec("f.write({})".format(repr(data[i:i + 128])))
        self.exec("f.close()")

    def mkdir(self, path):
        self.exec(
            "import uos\n"
            "try:\n uos.mkdir('{}')\n"
            "except:\n pass".format(path)
        )

    def reset(self):
        self.ser.write(b'import machine\nmachine.reset()\x04')
        time.sleep(0.5)


def main():
    quick = len(sys.argv) > 1 and sys.argv[1].lower() == 'quick'
    files = QUICK_FILES if quick else FULL_FILES
    mode = 'quick' if quick else 'full'

    print()
    print("MotionSprinkler deploy | {} | {}".format(PORT, mode))
    print("-" * 44)
    print("NOTE: Jupyter kernel must be disconnected from {} first.".format(PORT))
    print()

    repl = RawREPL(PORT, BAUD)
    try:
        print("Connecting to device...")
        repl.enter()

        if not quick:
            print("Ensuring app/ directory exists...")
            repl.mkdir('app')

        for path in files:
            if not os.path.exists(path):
                print("  SKIP (not found): {}".format(path))
                continue
            print("  > {}".format(path))
            repl.upload(path)

        print("Resetting device...")
        repl.reset()

    except RuntimeError as e:
        print("\nERROR:", e)
        repl.close()
        sys.exit(1)

    repl.close()
    print()
    print("Deploy complete. Device is rebooting.")
    if not quick:
        print("Zone defaults: Z1=GPIO17  Z2=GPIO19  Z3=GPIO20  Z4=GPIO21")


if __name__ == '__main__':
    main()
