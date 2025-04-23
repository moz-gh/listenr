import configparser
import os
import sys
import logging
import re # Import regex module for parsing commands

APP_NAME = 'asr-indicator' # Consistent App Name
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", APP_NAME)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.ini")

# Define defaults directly here for clarity
DEFAULT_CONFIG = {
    'Paths': {
        'temporary_audio_dir': '/tmp',
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
        'leading_silence_s': '0.2',  # Moved from VAD for broader use
        'trailing_silence_s': '0.3', # Moved from VAD for broader use
    },
    'VAD': {
        'speech_threshold': '0.5',
        'silence_duration_ms': '700',
        'frame_ms': '32',
        # Padding moved to Audio section
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
        'active': 'media-record',
        'paused': 'media-playback-pause',
        'processing': 'preferences-system-time',
        'success': 'emblem-ok',
        'error': 'dialog-error',
        'app_icon': 'audio-input-microphone'
    },
    # --- NEW [Commands] Section ---
    # Define default commands here using the 'trigger = regex ::: action' format
    # Use Python raw strings (r"...") for regex patterns
    'Commands': {
        'open_browser': r"^(?:(?:please|can you)\s+)?open(?:\s+the)?\s+browser[\s,.!?]*$ ::: firefox",
        'search_web': r'^(?:(?:please|can you)\s+)?(?:search|find)\s+(?:web\s+)?for\s+(?:um|uh)?\s*(.+?)[\s,.!?]*$ ::: firefox https://duckduckgo.com/?q="{0}"',
        'run_backup': r"^(?:please\s+)?run(?:\s+the)?\s+backup\s+script[\s,.!?]*$ ::: /home/gabriel/Scripts/my_backup.sh",
        'new_meeting_note': r"^(?:create|make|new)(?:\s+a)?\s+(?:meeting\s+)?note[\s,.!?]*$ ::: NEW_NOTE::/home/gabriel/Documents/Meetings/",
        'log_alpha': r"^log(?:\s+to)?\s+project\s+alpha\s+(?:um|uh)?\s*(.+)$ ::: APPEND_NOTE::/home/gabriel/Projects/Alpha/log.txt",
        'ask_llm': r'^(?:ask|tell|query)(?:\s+the)?\s+llm\s+(?:about|for|uh|um)?\s*(.+?)\.?[\s,.!?]*$ ::: /home/gabriel/Code/llm-scripts/query.sh "{0}"',
        # Add more default commands if desired
    }
}

# Initialize ConfigParser instance
config = configparser.ConfigParser(
    inline_comment_prefixes=('#', ';'),
    interpolation=None # Disable interpolation to treat % signs literally
)

def load_config():
    """Loads config, creates default if needed, returns config object."""
    global config
    # --- Make Config Loading More Robust ---
    # Reset parser and read defaults first to ensure all sections/defaults exist
    config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'), interpolation=None)
    config.read_dict(DEFAULT_CONFIG)

    if not os.path.exists(CONFIG_FILE):
        print(f"Config file not found. Creating default at: {CONFIG_FILE}")
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            # Write the fully populated config object (containing defaults)
            with open(CONFIG_FILE, 'w') as configfile:
                config.write(configfile)
            print("Default config file created. Please review it, especially [Commands].")
        except OSError as e:
            print(f"ERROR: Could not create default config: {e}", file=sys.stderr)
            # Keep using internal defaults loaded via read_dict
            return config # Return config object containing only defaults

    try:
        # Read user's file, overriding defaults where specified
        # This will correctly load or override the [Commands] section too
        loaded_files = config.read(CONFIG_FILE)
        if loaded_files:
            print(f"Loaded config from {CONFIG_FILE}")
        else:
             # This case should ideally not happen due to the creation logic above
             # but good to handle if the file becomes unreadable later.
             print(f"Warning: Could not read config file {CONFIG_FILE} though it exists. Using internal defaults.")
             config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'), interpolation=None)
             config.read_dict(DEFAULT_CONFIG)


    except configparser.Error as e:
        print(f"ERROR reading config file {CONFIG_FILE}: {e}", file=sys.stderr)
        print("Using internal defaults.", file=sys.stderr)
        # Reset to defaults on error after trying to load
        config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'), interpolation=None)
        config.read_dict(DEFAULT_CONFIG)

    # --- Perform Validation (existing validation remains) ---
    try:
        get_int_setting('Audio', 'sample_rate')
        get_int_setting('Audio', 'channels')
        get_int_setting('Whisper', 'beam_size')
        get_bool_setting('UI', 'show_notifications')
        get_float_setting('VAD', 'speech_threshold')
        get_int_setting('VAD', 'silence_duration_ms')
        get_int_setting('VAD', 'frame_ms')
        get_float_setting('Audio', 'leading_silence_s', allow_zero=True)
        get_float_setting('Audio', 'trailing_silence_s', allow_zero=True)

        # Check VAD requirements (existing code)
        sr = get_int_setting('Audio', 'sample_rate')
        if sr not in [8000, 16000]:
             logging.warning(f"VAD typically requires sample rate 8000 or 16000, configured: {sr}")
        frame_ms = get_int_setting('VAD', 'frame_ms')
        frame_size = int(sr * frame_ms / 1000)
        valid_silero_sizes_16k = [256, 512, 768, 1024, 1536]
        if sr == 16000 and frame_size not in valid_silero_sizes_16k:
             logging.warning(f"Silero VAD frame_ms={frame_ms} results in frame size {frame_size} at {sr}Hz. Expected sizes: {valid_silero_sizes_16k}. VAD might fail.")

    except ValueError as e:
        print(f"ERROR: Config file has invalid number format: {e}", file=sys.stderr)
        print("Please check numeric settings (e.g., sample_rate, threshold, durations).", file=sys.stderr)
        sys.exit(1) # Exit if fundamentally broken

    return config

# --- Helper functions for getting settings (modified fallbacks slightly) ---
def get_setting(section, key, fallback_value=None):
    """Gets a string setting, falling back to default dict then provided fallback."""
    default_from_dict = DEFAULT_CONFIG.get(section, {}).get(key)
    # Use provided fallback only if not found in user config AND not in default dict
    final_fallback = fallback_value if default_from_dict is None else default_from_dict
    return config.get(section, key, fallback=final_fallback)

def get_int_setting(section, key, fallback_value=0):
    """Gets an int setting, falling back safely."""
    default_from_dict_str = DEFAULT_CONFIG.get(section, {}).get(key)
    try:
        # Prioritize fallback_value if default dict entry is invalid or missing
        final_fallback = int(default_from_dict_str) if default_from_dict_str is not None else fallback_value
    except (ValueError, TypeError):
        final_fallback = fallback_value

    try:
        return config.getint(section, key, fallback=final_fallback)
    except (ValueError, TypeError):
        print(f"Warning: Invalid integer for [{section}]{key}. Using fallback {final_fallback}.", file=sys.stderr)
        return final_fallback

def get_float_setting(section, key, fallback_value=0.0):
    """Gets a float setting, falling back safely."""
    default_from_dict_str = DEFAULT_CONFIG.get(section, {}).get(key)
    try:
        final_fallback = float(default_from_dict_str) if default_from_dict_str is not None else fallback_value
    except (ValueError, TypeError):
        final_fallback = fallback_value

    try:
        return config.getfloat(section, key, fallback=final_fallback)
    except (ValueError, TypeError):
        print(f"Warning: Invalid float for [{section}]{key}. Using fallback {final_fallback}.", file=sys.stderr)
        return final_fallback

def get_bool_setting(section, key, fallback_value=False):
    """Gets a boolean setting, falling back safely."""
    default_from_dict_str = DEFAULT_CONFIG.get(section, {}).get(key)
    try:
        final_fallback = default_from_dict_str.lower() == 'true' if default_from_dict_str is not None else fallback_value
    except AttributeError: # Handle if default is not string
         final_fallback = bool(default_from_dict_str) if default_from_dict_str is not None else fallback_value

    try:
        return config.getboolean(section, key, fallback=final_fallback)
    except (ValueError, TypeError):
        print(f"Warning: Invalid boolean for [{section}]{key}. Using fallback {final_fallback}.", file=sys.stderr)
        return final_fallback

def get_temp_audio_dir():
    dir_path = get_setting('Paths', 'temporary_audio_dir', fallback_value='/tmp')
    return os.path.expanduser(dir_path)

# --- NEW: Function to parse and retrieve commands ---
def get_commands():
    """
    Parses the [Commands] section of the config.

    Returns:
        dict: A dictionary where keys are trigger names and values are
              dicts {'regex': compiled_regex_object, 'action': action_string}.
              Returns an empty dict if the section is missing or empty.
    """
    commands = {}
    if not config.has_section('Commands'):
        logging.warning("No [Commands] section found in config.")
        return commands

    for trigger_name, value_str in config.items('Commands'):
        try:
            # Split only on the first occurrence of ':::'
            regex_pattern, action = map(str.strip, value_str.split(':::', 1))

            if not regex_pattern or not action:
                logging.warning(f"Skipping invalid command '{trigger_name}': Empty regex or action.")
                continue

            try:
                # Compile the regex for efficiency and validation
                # Add re.IGNORECASE if case-insensitivity is desired by default
                compiled_regex = re.compile(regex_pattern, re.IGNORECASE) # Added IGNORECASE
                commands[trigger_name] = {
                    'regex': compiled_regex,
                    'action': action
                }
                logging.debug(f"Loaded command '{trigger_name}': regex='{regex_pattern}', action='{action}'")
            except re.error as e:
                logging.warning(f"Skipping invalid command '{trigger_name}': Invalid regex pattern '{regex_pattern}'. Error: {e}")

        except ValueError:
            # Catches error if split doesn't produce exactly two parts
            logging.warning(f"Skipping invalid command format for '{trigger_name}'. Ensure 'regex ::: action' format.")
        except Exception as e:
             logging.error(f"Unexpected error processing command '{trigger_name}': {e}")

    return commands


# --- Load config when the module is imported ---
# Ensure logging is configured before this if warnings are important at startup
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s') # Example basic config
config = load_config()

# You can now import 'get_commands' from this module elsewhere in your app
# Example usage in another file:
# from config_manager import get_commands
# registered_commands = get_commands()
# for name, details in registered_commands.items():
#     match = details['regex'].match(user_input)
#     if match:
#         action = details['action']
#         # Process action using match.groups() etc.