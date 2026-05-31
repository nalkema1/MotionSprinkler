@ECHO OFF
IF "%1"=="" GOTO Continue
  ampy --port COM14 --baud 115200 %*
:Continue