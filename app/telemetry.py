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

    # Check if the file size is greater than 300k and overwrite if necessary
    try:
        if os.stat(filename).st_size > 300 * 1024:
            with open(filename, "w") as file:
                file.write(formatted_logdata + "\n")
        else:
            # Write the log data to the file
            with open(filename, "a") as file:
                file.write(formatted_logdata + "\n")
    except OSError:
        # If the file does not exist, create it by writing the log data
        with open(filename, "w") as file:
            file.write(formatted_logdata + "\n")

    return
