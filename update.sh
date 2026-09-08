#!/usr/bin/env bash
# Update script for PiClock.
# This script prepares the Python environment and then runs update.py,
# which handles the actual PiClock update work.

# Make the script safer and easier to troubleshoot:
# -E keeps error traps active inside functions.
# -e stops the script if a command fails.
# -u stops the script if it tries to use a variable that was never set.
# -o pipefail makes a pipeline fail if any command in the pipeline fails,
#   not just the last command.
set -Eeuo pipefail

# Find the folder where this update script lives.
# This lets the script work even if it is started from another folder.
#
# ${BASH_SOURCE[0]} is this script's filename.
# dirname gets the folder part of that filename.
# cd moves into that folder.
# pwd prints the full path to that folder.
# readonly means PROJECT_DIR should not be changed later.
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_DIR

# The virtual environment is a private Python setup for PiClock.
# It keeps PiClock's Python packages separate from the rest of the system.
VENV_DIR="$PROJECT_DIR/venv"
readonly VENV_DIR

# Remember whether this script activated the virtual environment.
# This starts as false and is changed to true after activation succeeds.
VENV_ACTIVE=false

cleanup() {
  # If this script activated the virtual environment, turn it off before exiting.
  # This runs whether the update succeeds or fails because of the trap below.
  if [[ "$VENV_ACTIVE" == true ]]; then
    echo "Deactivating virtual environment"
    deactivate
  fi
}

# Always run cleanup when the script exits.
# EXIT means this happens at the end of the script, even if an error stops it early.
trap cleanup EXIT

create_virtual_environment() {
  echo "Virtual environment not found"
  echo "Installing Python 3 and virtual-environment support..."

  # Update the Raspberry Pi package list, then install the full Python 3 package.
  # python3-full includes the pieces needed to create virtual environments.
  sudo apt update
  sudo apt install --yes python3-full

  echo "Creating virtual environment..."

  # Create PiClock's private Python environment.
  # --system-site-packages lets the environment also see Python packages
  # installed for the whole system, which can be useful on Raspberry Pi.
  python3 -m venv --system-site-packages "$VENV_DIR"
}

# Move into the PiClock project folder before doing any update work.
cd -- "$PROJECT_DIR"

# If the virtual environment does not exist yet, create it.
# The activate file is what Bash uses to turn the virtual environment on.
if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
  create_virtual_environment
fi

echo "Activating virtual environment..."
# Load PiClock's private Python environment into this shell.
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Mark the environment as active so cleanup knows it should deactivate it later.
VENV_ACTIVE=true

echo "Running updates for PiClock..."

# Run the Python update script using the Python from the virtual environment.
python update.py
