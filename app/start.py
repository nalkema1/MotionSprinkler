import time
from app.timesync import ntpsync
from app.timesync import myTime
from app.timesync import myTimeAsDict
from app.ota_updater import OTAUpdater
from app.wifi_manager import WifiManager
from app.motiondetect import motion
from app.telemetry import sendTelemetry
import machine
from machine import Pin
from time import sleep
import urequests
import ujson
import binascii
import os
import esp32
import gc

prod = True # run with network
bypassupdate = False
reboot = False
start_time = time.ticks_ms()
power_on = time.ticks_ms()
data = {}

def ConnectToNetwork(reset_action):

    global wm

    if not wm.is_connected():
        for retries in range(5):
            wm.connect(prod, reset_action)
            if wm.is_connected():
                break
            else:
                time.sleep(1)
        ntpsync()
            
def CheckSchedule(timer):

    if gc.mem_free() < 100000:
        gc.collect()
    if gc.mem_free() < 40000:
        sendTelemetry("low memory, resetting system")
        machine.reset()

    if time.ticks_diff(time.ticks_ms(), power_on) > 86400000:
        sendTelemetry("time for a daily reset")
        machine.reset()

def myaction():
    global on
    global start
    if not on:
        start = time.time()
        on = True
        sendTelemetry("MotionSprinkler action executed")
        relay2.value(1)
        time.sleep(8)
        relay2.value(0)
    else:
        print("already running")

power_on = time.ticks_ms()
on = False
start = time.time()
relay2 = Pin(17, Pin.OUT)

mymotion = motion(14, myaction, True)
timer = machine.Timer(0)  
timer.init(period=60000, mode=machine.Timer.PERIODIC, callback=CheckSchedule)
machine.freq(80000000)

MyResetCause = machine.reset_cause()
resetstr = "Unknown - "+str(MyResetCause)
if ( MyResetCause == machine.PWRON_RESET ): resetstr = "PWRON_RESET"
if ( MyResetCause == machine.HARD_RESET ): resetstr = "HARD_RESET"
if ( MyResetCause == machine.WDT_RESET ): resetstr = "WDT_RESET"
if ( MyResetCause == machine.DEEPSLEEP_RESET ): resetstr = "DEEPSLEEP_RESET"
if ( MyResetCause == machine.SOFT_RESET ): resetstr = "SOFT_RESET"

sendTelemetry(f"Boot status : {resetstr}")

wm = WifiManager()
ConnectToNetwork(resetstr)

if wm.is_connected():
    currentTime = myTimeAsDict()
    try:
        with open('last_update.txt','r') as f:
            data = ujson.loads(f.read())
    except:
        data["day"] = 99

    otaUpdater = OTAUpdater('https://github.com/nalkema1/MotionSprinkler', main_dir='app', headers={'Accept': 'application/vnd.github.v3+json'})
    current_version = otaUpdater.get_version(otaUpdater.modulepath(otaUpdater.main_dir))
    sendTelemetry(f"Current Version : {current_version}")
    if not bypassupdate and data["day"] != currentTime["day"]:
        sendTelemetry("Checking for software update....")
        try:

            hasUpdated = otaUpdater.install_update_if_available()
            if hasUpdated:
                machine.reset()
            else:
                del(otaUpdater)
                gc.collect()

            with open('last_update.txt','w') as f:
                ujson.dump(currentTime, f)
        except:
            sendTelemetry("OTA Updated failed")


while True:
    if mymotion.motiondetected():
        print("in the loop")
    if on and (time.time() - start > 12):
        print("action reset")
        on = False
