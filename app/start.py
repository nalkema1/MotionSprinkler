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

prod = True # run without network
reboot = False
start_time = time.ticks_ms()
power_on = time.ticks_ms()
data = {}


def setCPU(size):
    if size == 3:
        print("CPU set to 16 mhz")
        machine.freq(160000000)
        return
    if size == 2:
        print("CPU set to 8 mhz")
        machine.freq(80000000)
        return
    else:
        print("CPU set to 4 mhz")
        machine.freq(40000000)

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

    if data["day"] != currentTime["day"]:
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

on = False
start = time.time()
relay2 = Pin(17, Pin.OUT)

def myaction():
    global on
    global start
    if not on:
        start = time.time()
        on = True
        print("My action executed")
        relay2.value(1)
        time.sleep(5)
        relay2.value(0)
    else:
        print("already running")

mymotion = motion(14, myaction, True)

while True:
    if mymotion.motiondetected():
        print("in the loop")
    if on and (time.time() - start > 10):
        print("action reset")
        on = False
