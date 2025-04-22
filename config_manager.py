import configparser
import os
import sys
import logging

APP_NAME = 'asr-indicator' # Consistent App Name
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", APP_NAME)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.ini")

# Define defaults directly here for clarity
DEFAULT_CONFIG = {
    'Paths': {
        # No longer writing one big file, but keep dir for potential segment debugging?
        'temporary_audio_dir': '/tmp',
        # temporary_audio_filename = whisper_recording.wav # Less relevant now
    },
    'Whisper': {
        'model_size': 'medium.en',
        'device': 'cpu',
        'compute_type': 'int8',
        'beam_size': '5',
    },
    'Audio': {
        'input_device': 'default',
        'sample_rate': '16000', # VAD models often require 16000 or 8000
        'channels': '1',
    },
    # --- NEW VAD Section ---
    'VAD': {
        # Lower threshold = more sensitive to speech (might pick up noise)
        # Higher threshold = less sensitive (might miss quiet speech)
        'speech_threshold': '0.5',
        # How long silence must be before triggering end-of-speech (seconds)
        'silence_duration_ms': '700',
        # How often VAD checks audio (should be small, e.g., 30ms) - depends on VAD model
        'frame_ms': '32',
        # Optional padding at start/end of speech segments (seconds)
        'leading_silence_s': '0.2',
        'trailing_silence_s': '0.3',
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
        'active': 'media-record', # Changed from 'recording'
        'paused': 'media-playback-pause', # New icon for paused state
        'processing': 'preferences-system-time',
        'success': 'emblem-ok',
        'error': 'dialog-error',
        'app_icon': 'audio-input-microphone'
    }
}

config = configparser.ConfigParser(inline_comment_prefixes=('#', ';')) # Allow comments

def load_config():
    """Loads config, creates default, returns config object."""
    global config
    # --- Make Config Loading More Robust ---
    # Use read_dict first to ensure all defaults are present in the object
    config.read_dict(DEFAULT_CONFIG)

    if not os.path.exists(CONFIG_FILE):
        print(f"Config file not found. Creating default at: {CONFIG_FILE}")
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            # Write the fully populated config object
            with open(CONFIG_FILE, 'w') as configfile:
                config.write(configfile)
            print("Default config file created. Please review it.")
        except OSError as e:
            print(f"ERROR: Could not create default config: {e}", file=sys.stderr)
            # Keep using internal defaults loaded via read_dict
            return config
    try:
        # Read user's file, overriding defaults where specified
        config.read(CONFIG_FILE)
        print(f"Loaded config from {CONFIG_FILE}")

    except configparser.Error as e:
        print(f"ERROR: reading config file {CONFIG_FILE}: {e}", file=sys.stderr)
        print("Using internal defaults.", file=sys.stderr)
        # Reset to defaults on error after trying to load
        config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
        config.read_dict(DEFAULT_CONFIG)

    # --- Perform Validation ---
    try:
        # Use helper functions for cleaner validation/access
        get_int_setting('Audio', 'sample_rate')
        get_int_setting('Audio', 'channels')
        get_int_setting('Whisper', 'beam_size')
        get_bool_setting('UI', 'show_notifications')
        get_float_setting('VAD', 'speech_threshold')
        get_int_setting('VAD', 'silence_duration_ms')
        get_int_setting('VAD', 'frame_ms')
        get_float_setting('Audio', 'leading_silence_s', allow_zero=True) # Allow zero padding
        get_float_setting('Audio', 'trailing_silence_s', allow_zero=True)

        # Check VAD requirements
        sr = get_int_setting('Audio', 'sample_rate')
        if sr not in [8000, 16000]:
             logging.warning(f"VAD typically requires sample rate 8000 or 16000, configured: {sr}")
        frame_ms = get_int_setting('VAD', 'frame_ms')
        # Silero VAD expects frame sizes of 256, 512, 768, 1024, 1536 samples for 16k
        # Check if frame_ms corresponds to a valid size
        frame_size = int(sr * frame_ms / 1000)
        valid_silero_sizes_16k = [256, 512, 768, 1024, 1536]
        if sr == 16000 and frame_size not in valid_silero_sizes_16k:
             logging.warning(f"Silero VAD frame_ms={frame_ms} results in frame size {frame_size} at {sr}Hz. Expected sizes: {valid_silero_sizes_16k}. VAD might fail.")
        # Add similar checks for 8k if needed

    except ValueError as e:
        print(f"ERROR: Config file has invalid number format: {e}", file=sys.stderr)
        print("Please check numeric settings (e.g., sample_rate, threshold, durations).", file=sys.stderr)
        sys.exit(1) # Exit if fundamentally broken

    return config

# --- Helper functions for getting settings ---
def get_setting(section, key):
    return config.get(section, key, fallback=DEFAULT_CONFIG.get(section, {}).get(key, ''))

def get_int_setting(section, key):
    try:
        return config.getint(section, key, fallback=int(DEFAULT_CONFIG.get(section, {}).get(key, 0)))
    except (ValueError, TypeError): # Catch potential errors during fallback conversion too
        print(f"Warning: Invalid integer for [{section}]{key}. Using default.", file=sys.stderr)
        return int(DEFAULT_CONFIG.get(section, {}).get(key, 0))

def get_float_setting(section, key, allow_zero=False):
     default_val_str = DEFAULT_CONFIG.get(section, {}).get(key, '0.0')
     default_val = 0.0 if allow_zero else float(default_val_str) if default_val_str else 0.0
     try:
         val = config.getfloat(section, key, fallback=default_val)
         return val if allow_zero or val > 0 else default_val # Ensure > 0 if not allow_zero
     except (ValueError, TypeError):
        print(f"Warning: Invalid float for [{section}]{key}. Using default.", file=sys.stderr)
        return default_val

def get_bool_setting(section, key):
    try:
        # Be explicit about fallback conversion
        default_bool = DEFAULT_CONFIG.get(section, {}).get(key, 'false').lower() == 'true'
        return config.getboolean(section, key, fallback=default_bool)
    except (ValueError, TypeError):
        print(f"Warning: Invalid boolean for [{section}]{key}. Using default.", file=sys.stderr)
        default_bool = DEFAULT_CONFIG.get(section, {}).get(key, 'false').lower() == 'true'
        return default_bool

def get_temp_audio_dir(): # Changed name, not returning full path anymore
    dir_path = get_setting('Paths', 'temporary_audio_dir')
    return os.path.expanduser(dir_path)

# Load config on import
config = load_config()