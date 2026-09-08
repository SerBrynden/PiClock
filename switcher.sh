#!/usr/bin/env bash
# Switch skins by restarting PiClock with an alternate configuration.
# Designed to be started from crontab, for example:
# 0 8 * * * bash /home/pi/PiClock/switcher.sh Config
# 0 21 * * * bash /home/pi/PiClock/switcher.sh Config-Night

# Make the script safer and easier to troubleshoot:
# -E keeps error traps active inside functions.
# -e stops the script if a command fails.
# -u stops the script if it tries to use a variable that was never set.
# -o pipefail makes a pipeline fail if any command in the pipeline fails,
#   not just the last command.
set -Eeuo pipefail

# Find the folder where this switcher script lives.
# This lets the script work even if it is started from another folder.
#
# ${BASH_SOURCE[0]} is this script's filename.
# dirname gets the folder part of that filename.
# cd moves into that folder.
# pwd prints the full path to that folder.
# readonly means PROJECT_DIR should not be changed later.
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_DIR

# The virtual environment is PiClock's private Python setup.
# It keeps PiClock's Python packages separate from the rest of the system.
VENV_DIR="$PROJECT_DIR/venv"
readonly VENV_DIR

# The Clock folder contains the main PiClock Python program.
CLOCK_DIR="$PROJECT_DIR/Clock"
readonly CLOCK_DIR

die() {
  # Print an error message for PiClock.
  # "$*" means "all words passed to this function".
  # >&2 sends the message to the error output instead of normal output.
  echo "PiClock: $*" >&2
  exit 1
}

stop_clock() {
  local status

  echo "Stopping PiClock..."
  # Stop the currently running PiClock program before starting it again.
  # pkill searches running programs and sends them a signal.
  # -INT asks PiClock to stop cleanly, similar to pressing Ctrl+C.
  # -f means search the full command line, not just the program name.
  # -- marks the end of pkill options, so the search text is treated safely.
  #
  # The bracket in '[P]yQtPiClock.py' prevents this pkill command itself
  # from matching the pattern while it searches for PiClock.
  pkill -INT -f -- '[P]yQtPiClock.py' || {
    status=$?
    # pkill returns 1 when no matching process exists.
    # That is okay here because it just means PiClock was not already running.
    # Any other return value means something unexpected happened.
    [[ "$status" -eq 1 ]] || die "could not stop the running PiClock process"
  }
}

# Move into the PiClock project folder before running anything else.
# If that fails, stop with a clear error message.
cd -- "$PROJECT_DIR" || die "cannot change to $PROJECT_DIR"

# Stop the old PiClock process so the new one can start with the selected settings.
stop_clock

echo "Activating virtual environment..."
# Load PiClock's private Python environment into this shell.
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Tell graphical programs which screen to use.
# If DISPLAY is already set, keep it. Otherwise default to :0, the usual Raspberry Pi screen.
export DISPLAY="${DISPLAY:-:0}"

# Tell the Python program to write daily rotating log files.
export PICLOCK_DAILY_LOG=1

echo "Starting PiClock... logging to Clock/PyQtPiClock.1.log (rotates daily at midnight)"
# Replace this shell script with the PiClock Python program.
# python3 -u runs Python with unbuffered output so log messages appear immediately.
# "$@" passes along any command-line options given to this script.
exec python3 -u "$CLOCK_DIR/PyQtPiClock.py" "$@"
