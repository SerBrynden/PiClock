#!/bin/bash
cd "$HOME"/PiClock || exit
pkill -INT -f PyQtPiClock.py
# virtual environment
echo "Activating virtual environment..."
source venv/bin/activate || exit
# the main app
cd Clock || exit
if [ "$DISPLAY" = "" ]; then
  export DISPLAY=:0
fi
echo "Starting PiClock... logging to Clock/PyQtPiClock.1.log (rotates daily at midnight)"
PICLOCK_DAILY_LOG=1 python3 -u PyQtPiClock.py "$1"
