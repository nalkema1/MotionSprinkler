import time
import uos

def myTime(UTC_OFFSET=-14400):
    """
    UTC_OFFSET = -4 * 60 * 60   # change the '-4' according to your timezone
    """
    return time.localtime(time.time() + UTC_OFFSET)

def sendTelemetry(logdata, force_new_file=False):
    if logdata is None or logdata == "None":
        return

    # Get the current date and time to create a timestamp
    current_time = myTime()
    # MicroPython's time module doesn't have strftime, so we format it manually
    timestamp = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
        current_time[0], current_time[1], current_time[2],
        current_time[3], current_time[4], current_time[5]
    )
    formatted_logdata = f"{timestamp}, {logdata}"

    print(f"logdata : {formatted_logdata}")

    # Get the current date to create a filename
    filename = "telemetry.csv"

    # Check if the file exists and if the file size is greater than 15000 bytes and force a new file if it is
    try:
        if uos.stat(filename)[6] > 15000:
            force_new_file = True
    except OSError:
        # If the file does not exist, we will create a new one anyway
        force_new_file = True

    # Determine file mode based on force_new_file flag
    file_mode = "w" if force_new_file else "a"

    # Write the log data to the file
    try:
        with open(filename, file_mode) as file:
            file.write(formatted_logdata + "\n")
    except OSError as e:
        print(f"Failed to write to file: {e}")

    return
