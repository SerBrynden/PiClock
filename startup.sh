#!/bin/bash
# Startup script for the PiClock.
# Designed to be started from PiClock.desktop (autostart), or from crontab:
# @reboot bash /home/pi/PiClock/startup.sh

# If the script tries to use an unset variable, treat it as an error and stop the script.
# This helps catch typos and missing values early instead of continuing silently.
set -u

# Find the folder where this startup script lives.
# This lets the script work even if it is launched from another folder.
#
# ${BASH_SOURCE[0]} is this script's filename.
# dirname gets the folder part of that filename.
# cd moves into that folder.
# pwd prints the full path to that folder.
# readonly means PICLOCK_DIR should not be changed later.
PICLOCK_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PICLOCK_DIR

die() {
  # Print an error message for PiClock.
  # "$*" means "all words passed to this function".
  # >&2 sends the message to the error output instead of normal output.
  echo "PiClock: $*" >&2
  exit 1
}

is_running() {
  # Check whether a program or command is already running.
  # pgrep searches the list of running processes.
  # -f means search the full command line, not just the program name.
  # -- marks the end of pgrep options, so the search text is treated safely.
  #
  # >/dev/null 2>&1 hides both normal output and error messages.
  # We only care whether pgrep succeeds or fails, not what it prints.
  pgrep -f -- "$1" >/dev/null 2>&1
  # >/dev/null 2>&1 means:
  #   > /dev/null  = throw away normal output
  #   2>&1         = send error output to the same place as normal output
  # In short: run quietly unless the script checks the command's success or failure.
}

wait_before_starting() {
  local mode="delay"
  local seconds=45

  # Look at the first command-line option, if one was provided.
  # ${1:-} means "use the first argument, or use an empty value if there is none".
  # This avoids an error because set -u is enabled above.
  case "${1:-}" in
    -n|--no-sleep|--no-delay)
      # Start immediately.
      mode="none"
      shift
      ;;
    -d|--delay)
      # A delay option must be followed by a number of seconds.
      # "$#" is the number of command-line arguments currently available.
      # -ge means "greater than or equal to".
      # If there are not at least 2 arguments, show an error and stop.
      [ "$#" -ge 2 ] || die "$1 requires a number of seconds"
      seconds="$2"
      shift 2
      ;;
    -m|--message-delay)
      # Like --delay, but show a message box during the wait.
      [ "$#" -ge 2 ] || die "$1 requires a number of seconds"
      seconds="$2"
      mode="message"
      shift 2
      ;;
  esac

  case "$mode" in
    none)
      # Do not wait.
      ;;
    delay)
      echo "Waiting $seconds seconds before starting"
      sleep "$seconds"
      ;;
    message)
      echo "Waiting $seconds seconds for response before starting"
      # zenity shows a small graphical question window.
      # >/dev/null 2>&1 hides text output from zenity because the window is all the user needs.
      # zenity returns 1 when the user chooses Cancel.
      if zenity --question --title=PiClock --ok-label=Now \
          --cancel-label=Cancel --timeout="$seconds" \
          --text="Starting PiClock in $seconds seconds" >/dev/null 2>&1; then
        :
      elif [ "$?" -eq 1 ]; then
        echo "PiClock Cancelled"
        exit 0
      fi
      ;;
  esac

  # Return the arguments left after the startup option through a global array.
  WAIT_REMAINING=("$@")
}

# Move into the PiClock project folder before running anything else.
# If that fails, stop with a clear error message.
cd -- "$PICLOCK_DIR" || die "cannot change to $PICLOCK_DIR"

# Tell graphical programs which screen to use.
# If DISPLAY is already set, keep it. Otherwise default to :0, the usual Raspberry Pi screen.
export DISPLAY="${DISPLAY:-:0}"

wait_before_starting "$@"
set -- "${WAIT_REMAINING[@]}"

# Show a short "Starting PiClock..." message.
# The final & runs zenity in the background so the script can keep going.
zenity --info --timeout=3 --text="Starting PiClock..." >/dev/null 2>&1 &

echo "Disabling screen blanking..."
xset s off
xset -dpms
xset s noblank

if ! is_running unclutter; then
  # Start unclutter, which hides the mouse pointer when it is not being used.
  # >/dev/null 2>&1 hides normal and error output.
  # & runs it in the background so PiClock can continue starting.
  unclutter >/dev/null 2>&1 &
fi

echo "Setting sound to max (assuming Monitor Tv controls volume)..."
# Set the audio volume using amixer.
# cset changes a sound-card control.
# numid=1 selects control number 1.
# 400 is the volume value used by this PiClock setup.
# Output is hidden because this is just startup housekeeping.
amixer cset numid=1 -- 400 >/dev/null 2>&1

echo "Activating virtual environment..."
source venv/bin/activate || exit 1

echo "Checking for NeoPixels Ambilight..."
# Try importing rpi_ws281x to see whether NeoPixel support is installed.
# Hide import-test output because only success or failure matters here.
# If the module exists and NeoAmbi.py is not already running, start it.
if python3 -c 'import rpi_ws281x' >/dev/null 2>&1 && ! is_running NeoAmbi.py; then
  echo "Starting NeoPixel Ambilight Service..."
  (cd Leds && sudo python3 NeoAmbi.py) &
fi

echo "Checking for GPIO Buttons..."
if [ -x Button/gpio-keys ] && ! is_running gpio-keys; then
  echo "Starting gpio-keys Service..."
  sudo Button/gpio-keys 23:KEY_SPACE 24:KEY_F2 25:KEY_UP &
fi

echo "Checking for Temperature Sensors..."
# Try importing w1thermsensor to see whether temperature sensor support is installed.
# If the module exists and TempServer.py is not already running, start it.
if python3 -c 'import w1thermsensor' >/dev/null 2>&1 && ! is_running TempServer.py; then
  echo "Starting Temperature Service..."
  (cd Temperature && python3 TempServer.py) &
fi

cd -- Clock || exit 1

# If the first remaining argument is -s or --screen-log, print PiClock logs to the screen.
# ${1:-} safely reads the first argument even when no argument was given.
if [ "${1:-}" = "-s" ] || [ "${1:-}" = "--screen-log" ]; then
  echo "Starting PiClock... logging to screen."
  python3 -u PyQtPiClock.py
else
  echo "Starting PiClock... logging to Clock/PyQtPiClock.1.log (rotates daily at midnight)"
  # PICLOCK_DAILY_LOG=1 tells the Python program to write daily rotating log files.
  # python3 -u runs Python with unbuffered output so log messages appear immediately.
  PICLOCK_DAILY_LOG=1 python3 -u PyQtPiClock.py
fi
