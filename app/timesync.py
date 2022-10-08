import ntptime
import time
from app.telemetry import sendTelemetry

def ntpsync(retries=10, retry_pause=.5):
    ntptime.host = "us.pool.ntp.org"
    ntpsync = False
    for retries in range(retries):    
        try:
            ntptime.settime()
            ntpsync = True
            break
        except:
            sendTelemetry("Error syncing time, retrying...")
    time.sleep(retry_pause)
    if ntpsync:
        sendTelemetry(f"Local time after synchronization{str(time.localtime())}")
    else:
        sendTelemetry(f"Error syncing time: {str(time.localtime())}" ) 

def myTime(UTC_OFFSET=14400):
    """
    UTC_OFFSET = -4 * 60 * 60   # change the '-4' according to your timezone
    """
    return time.localtime(time.time() + UTC_OFFSET)

def myTimeAsDict(UTC_OFFSET=14400):
    """
    UTC_OFFSET = -4 * 60 * 60 change the '-4' according to your timezone
    resul is a dictionary with the following keys:
    year, month, day, hour, min, min
    """
    ltime = time.localtime(time.time() + UTC_OFFSET)
    dict = {}
    dict["year"] = ltime[0]
    dict["month"] = ltime[1]
    dict["day"] = ltime[2]
    dict["hour"] = ltime[4]
    dict["min"] = ltime[5]
    dict["sec"] = ltime[6]
    return dict    