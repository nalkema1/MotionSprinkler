@ECHO OFF
IF "%1"=="" GOTO Continue
  ampy --port COM3 --baud 115200 %*
:Continue