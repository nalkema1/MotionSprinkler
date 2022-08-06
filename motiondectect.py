import esp32
from machine import Pin
import time

class motion():

    def __init__(self, activation_pin, action_method, debug=False) -> None:

        self.pir_pin = Pin(activation_pin, Pin.IN)
        self.pir_pin.irq(trigger=Pin.IRQ_RISING, handler=self.pir_action)
        self.action = action_method
        self.debug = debug
        self._motiondetected = False

    def pir_action(self, pin):
        self._motiondetected = True
        if self.debug:
            print(f"Motion detected on {time.ctime()}")
        self.action()
        # myaction()

    def motiondetected(self):
        if self._motiondetected:
            self._motiondetected = False
            return True

        return False

