import ntptime
import time

def ntpsync(retries=5, retry_pause=.3):
    ntptime.host = "us.pool.ntp.org"
    ntpsync = False
    for retries in range(retries):    
        try:
            ntptime.settime()
            ntpsync = True
            break
        except:
            print("Error syncing time, retrying...")
    time.sleep(retry_pause)
    if ntpsync:
        print("Local time after synchronization：%s" %str(time.localtime()))
    else:
        print("Error syncing time: %s" %str(time.localtime())) 

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