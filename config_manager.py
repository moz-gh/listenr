import configparser
import os
import sys

APP_NAME = 'asr-indicator' # Consistent App Name
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", APP_NAME)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.ini")

# Define defaults directly here for clarity
DEFAULT_CONFIG = {
    'Paths': {
        'temporary_audio_dir': '/tmp',
        'temporary_audio_filename': 'whisper_recording.wav',
    },
    'Whisper': {
        'model_size': 'medium.en',
        'device': 'cpu',
        'compute_type': 'int8',
        'beam_size': '5',
    },
    'Audio': {
        'input_device': 'default',
        'sample_rate': '16000',
        'channels': '1',
    },
    'Output': {
        'method': 'clipboard',
        'output_file': '',
    },
    'UI': {
        'hotkey_display': '',
        'show_notifications': 'true',
    },
    'Icons': {
         # Use generic names, rely on system theme or specify full paths
        'recording': 'media-record',
        'processing': 'preferences-system-time', # Example
        'success': 'emblem-ok', # Example
        'error': 'dialog-error',
        'app_icon': 'audio-input-microphone' # General icon for notifications
    }
}

config = configparser.ConfigParser()

def load_config():
    """Loads config, creates default, returns config object."""
    global config
    if not os.path.exists(CONFIG_FILE):
        print(f"Config file not found. Creating default at: {CONFIG_FILE}")
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            config.read_dict(DEFAULT_CONFIG)
            with open(CONFIG_FILE, 'w') as configfile:
                config.write(configfile)
            print("Default config file created. Please review it.")
        except OSError as e:
            print(f"ERROR: Could not create default config: {e}", file=sys.stderr)
            print("Using internal defaults.", file=sys.stderr)
            config.read_dict(DEFAULT_CONFIG) # Use internal on error
            return config # Return usable config
    try:
        config.read(CONFIG_FILE)
        # Merge with defaults to ensure all keys exist after reading
        for section, items in DEFAULT_CONFIG.items():
            if not config.has_section(section): config.add_section(section)
            for key, value in items.items():
                if not config.has_option(section, key): config.set(section, key, value)
    except configparser.Error as e:
        print(f"ERROR: reading config file {CONFIG_FILE}: {e}", file=sys.stderr)
        print("Using internal defaults.", file=sys.stderr)
        config.read_dict(DEFAULT_CONFIG) # Use internal on error

    # Basic validation (example)
    try:
         config.getint('Audio', 'sample_rate')
         config.getint('Audio', 'channels')
         config.getint('Whisper', 'beam_size')
         config.getboolean('UI', 'show_notifications')
    except ValueError as e:
         print(f"ERROR: Config file has invalid number format: {e}", file=sys.stderr)
         print("Please check sample_rate, channels, beam_size, show_notifications.", file=sys.stderr)
         sys.exit(1) # Exit if fundamentally broken

    return config

# Make config accessible after loading
config = load_config()

# Helper function to get config values easily
def get_setting(section, key):
    # Uses the globally loaded config object
    return config.get(section, key, fallback=DEFAULT_CONFIG.get(section, {}).get(key, ''))

def get_int_setting(section, key):
    try:
        return config.getint(section, key, fallback=int(DEFAULT_CONFIG.get(section, {}).get(key, 0)))
    except ValueError:
         print(f"Warning: Invalid integer for [{section}]{key} in config. Using default.", file=sys.stderr)
         return int(DEFAULT_CONFIG.get(section, {}).get(key, 0))

def get_bool_setting(section, key):
    try:
        return config.getboolean(section, key, fallback=DEFAULT_CONFIG.get(section, {}).get(key, 'false') == 'true')
    except ValueError:
        print(f"Warning: Invalid boolean for [{section}]{key} in config. Using default.", file=sys.stderr)
        return DEFAULT_CONFIG.get(section, {}).get(key, 'false') == 'true'

def get_temp_audio_path():
    dir_path = get_setting('Paths', 'temporary_audio_dir')
    filename = get_setting('Paths', 'temporary_audio_filename')
    return os.path.join(os.path.expanduser(dir_path), filename)

