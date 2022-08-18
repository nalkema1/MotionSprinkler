import time
from app.timesync import ntpsync
from app.timesync import myTime
from app.timesync import myTimeAsDict
from app.ota_updater import OTAUpdater
from app.wifi_manager import WifiManager
from app.motiondetect import motion
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

def ConnectToNetwork():

    global wm

    if not wm.is_connected():
        for retries in range(5):
            wm.connect(prod)
            if wm.is_connected():
                break
            else:
                time.sleep(1)
        ntpsync()


wm = WifiManager()
ConnectToNetwork()

if wm.is_connected():
    currentTime = myTimeAsDict()
    try:
        with open('last_update.txt','r') as f:
            data = ujson.loads(f.read())
    except:
        data["day"] = 99

    if not bypassupdate and data["day"] != currentTime["day"]:
        print("Checking for software update....")
        otaUpdater = OTAUpdater('https://github.com/nalkema1/MotionSprinkler', main_dir='app', headers={'Accept': 'application/vnd.github.v3+json'})

        hasUpdated = otaUpdater.install_update_if_available()
        if hasUpdated:
            machine.reset()
        else:
            del(otaUpdater)
            gc.collect()

        with open('last_update.txt','w') as f:
            ujson.dump(currentTime, f)

power_on = time.ticks_ms()
on = False
start = time.time()
relay2 = Pin(17, Pin.OUT)

def CheckSchedule(timer):
    
    if gc.mem_free() < 500000:
        print("low memory, resetting system")
        machine.reset()

    time_now = time.ticks_ms()
    if time.ticks_diff(time_now, power_on) > 3.6e+6:
        print("time for a daily reset")
        machine.reset()

def myaction():
    global on
    global start
    if not on:
        start = time.time()
        on = True
        print("My action executed")
        relay2.value(1)
        time.sleep(8)
        relay2.value(0)
    else:
        print("already running")

mymotion = motion(14, myaction, True)
timer = machine.Timer(0)  
timer.init(period=500000, mode=machine.Timer.PERIODIC, callback=CheckSchedule)

while True:
    if mymotion.motiondetected():
        print("in the loop")
    if on and (time.time() - start > 12):
        print("action reset")
        on = False
