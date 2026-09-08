"""Update PiClock's Python dependencies and migrate local configuration."""

from pathlib import Path
import re
import subprocess
import sys
from collections.abc import Sequence


# Find the main PiClock project folder.
# __file__ is this script, resolve() makes it a full path,
# and the parent property gets the folder containing this file.
PROJECT_DIR = Path(__file__).resolve().parent

# Files and folders that this update script may need to read or change.
REQUIREMENTS_FILE = PROJECT_DIR / "requirements.txt"
BUTTON_FILE = PROJECT_DIR / "Button" / "gpio-keys"
API_KEYS_FILE = PROJECT_DIR / "Clock" / "ApiKeys.py"
CONFIG_FILE = PROJECT_DIR / "Clock" / "Config.py"

# These patterns help the script recognize old or new settings in config files.
# They are used so the script can update files without needing to understand
# the whole Python file.
DEPRECATED_API_RE = re.compile(r"\s*(wuapi|dsapi|ccapi)\s*=")
TM_API_RE = re.compile(r"\s*tmapi\s*=")
OWM_API_RE = re.compile(r"\s*owmapi\s*=")
STARTUP_SCREEN_RE = re.compile(r"\s*startup_screen\s*=")
RAIN_VIEWER_RE = re.compile(r"\s*userainviewer\s*=")


def run_command(command: Sequence[str], *, check: bool = True) -> None:
    """Print and run a command."""
    # Show the command before running it so users can see what is happening.
    print(" ".join(command))

    # Run the command.
    # When check=True, Python stops with an error if the command fails.
    subprocess.run(command, check=check)


def update_packages() -> None:
    """Upgrade pip, remove the obsolete package, and install requirements."""
    # Each item is:
    #   1. A message to show the user
    #   2. The pip command arguments
    #   3. Whether failure should stop the update
    commands = (
        ("Updating Python Package Manager", ["install", "--upgrade", "pip"], True),
        ("Removing old Python Modules", ["uninstall", "python-metar", "-y"], False),
        ("Updating Python Modules", ["install", "-r", str(REQUIREMENTS_FILE)], True),
    )

    # Run each package-management step using the current Python interpreter.
    for message, arguments, check in commands:
        print(f"\n{message}")
        run_command([sys.executable, "-m", "pip", *arguments], check=check)


def update_button_permissions() -> None:
    # The gpio-keys helper needs executable permissions so the button service can run.
    print(f"\nChecking {BUTTON_FILE}")
    if BUTTON_FILE.is_file():
        print(
            f"Setting permissions on {BUTTON_FILE} to 744 "
            "(owner: read/write/run; everyone else: read-only)"
        )

        # 0o744 means:
        #   owner can read, write, and run it
        #   everyone else can only read it
        BUTTON_FILE.chmod(0o744)


def prompt_weather_provider() -> int:
    # Keep asking until the user chooses one of the supported weather providers.
    valid_choices = {"1", "2"}
    while True:
        print("Please select your weather provider:")
        print("  <1> OpenWeatherMap.org (https://openweathermap.org/price)")
        print("  <2> Tomorrow.io (https://www.tomorrow.io/weather-api/)")
        choice = input("Selection (1 or 2)? ").strip()
        if choice in valid_choices:
            return int(choice)
        print("Invalid selection. Please enter 1 or 2.")


def prompt_api_key(provider: int) -> str:
    # Ask for the correct type of API key based on the provider selected above.
    if provider == 1:
        print("Enter your OpenWeatherMap.org API key.")
    else:
        print("Enter your Tomorrow.io API key.")
    return input("key: ").strip()


def update_api_keys() -> None:
    print(f"\nChecking {API_KEYS_FILE}")

    # If the user's API key file does not exist, there is nothing to update.
    if not API_KEYS_FILE.is_file():
        return

    # Read the file as separate lines while keeping line endings intact.
    # Keeping line endings helps preserve the original file formatting.
    lines = API_KEYS_FILE.read_text(encoding="utf-8").splitlines(keepends=True)

    # Check whether the file already has one of the newer weather API keys.
    has_tm_api = any(TM_API_RE.match(line) for line in lines)
    has_owm_api = any(OWM_API_RE.match(line) for line in lines)

    # Remove old weather API settings that are no longer supported.
    updated_lines = [line for line in lines if not DEPRECATED_API_RE.match(line)]
    altered = len(updated_lines) != len(lines)

    # If no current weather API key exists, ask the user to add one now.
    if not has_tm_api and not has_owm_api:
        print("\nThis version of PiClock requires a new weather API key.")
        provider = prompt_weather_provider()
        api_key = prompt_api_key(provider)
        if len(api_key) > 1:
            key_name = "owmapi" if provider == 1 else "tmapi"
            updated_lines.append(f"{key_name} = {api_key!r}\n")
            altered = True

    # Only write the file if something actually changed.
    if altered:
        print(f"\nWriting updated {API_KEYS_FILE}")
        API_KEYS_FILE.write_text("".join(updated_lines), encoding="utf-8")
    else:
        print(f"No changes made to {API_KEYS_FILE}")


def warn_if_ws281x_is_missing() -> None:
    # rpi_ws281x is used for NeoPixel/Ambilight support.
    # Not every PiClock setup uses LEDs, so this is only a warning.
    try:
        # noqa: F401 tells code-checking tools that this import is intentionally unused.
        # Here, we only import rpi_ws281x to check whether it is installed.
        import rpi_ws281x  # noqa: F401
    except ModuleNotFoundError:
        print("\nWARNING: rpi_ws281x not found")
        print("NeoAmbi.py now uses rpi-ws281x/rpi-ws281x-python")
        print("Please install it as follows:")
        print("python3 -m pip install rpi_ws281x")


def update_config() -> None:
    print(f"Checking {CONFIG_FILE}")

    # If the user's main config file does not exist, there is nothing to update.
    if not CONFIG_FILE.is_file():
        return

    # Read the config while keeping original line endings.
    lines = CONFIG_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    additions = []

    # Add startup_screen if the user's config does not already define it.
    # This controls which PiClock screen is shown first.
    if not any(STARTUP_SCREEN_RE.match(line) for line in lines):
        print(f"Adding startup_screen to {CONFIG_FILE}")
        additions.append(
            "\n"
            "# Startup screen selection: 1 for screen 1 (clock), 2 for screen 2 (dual radar)\n"
            "startup_screen = 1\n"
        )

    # Add userainviewer if the user's config does not already define it.
    # This controls which weather radar source PiClock should use.
    if not any(RAIN_VIEWER_RE.match(line) for line in lines):
        print(f"Adding userainviewer to {CONFIG_FILE}")
        additions.append(
            "\nuserainviewer = 0  "
            "# 0 = LibreWXR, 1 = RainViewer (free tier max zoom 7)\n"
        )

    # Append any missing settings to the end of the config file.
    if additions:
        CONFIG_FILE.write_text("".join(lines) + "".join(additions), encoding="utf-8")
    else:
        print(f"No changes made to {CONFIG_FILE}")


def main() -> None:
    # Run each update step in order:
    #   1. Update Python packages
    #   2. Fix button helper permissions
    #   3. Migrate weather API keys if needed
    #   4. Warn about optional LED support
    #   5. Add any missing config settings
    update_packages()
    update_button_permissions()
    update_api_keys()
    warn_if_ws281x_is_missing()
    update_config()


if __name__ == "__main__":
    # Only run the update when this file is started directly.
    # This prevents the update from running if another script imports this file.
    main()
