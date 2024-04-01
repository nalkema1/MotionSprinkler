import time

def myTime(UTC_OFFSET=-14400):
    """
    UTC_OFFSET = -4 * 60 * 60   # change the '-4' according to your timezone
    """
    return time.localtime(time.time() + UTC_OFFSET)

def sendTelemetry(logdata):
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

    # Check if we need to recycle the file (i.e., if it's a new day)
    if current_time[3] == 0 and current_time[4] < 1:  # Accessing tuple elements directly
        # It's shortly after 12:00AM, overwrite the existing file
        with open(filename, "w") as file:
            file.write(formatted_logdata + "\n")
    else:
        # Write the log data to the file
        with open(filename, "a") as file:
            file.write(formatted_logdata + "\n")

    return
