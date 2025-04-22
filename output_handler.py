import pyperclip
import logging
import os
import time
import config_manager as cfg
from utils import send_notification

# Conditional import for pynput
try:
    from pynput.keyboard import Controller as KeyboardController
    pynput_available = True
except ImportError:
    logging.warning("pynput library not found. 'type' output method will not work.")
    pynput_available = False

def handle_output(text):
    """Performs the configured output action with the transcribed text."""
    method = cfg.get_setting('Output', 'method').lower()
    logging.info(f"Handling output using method: {method}")

    if not text:
         logging.warning("No text to output.")
         send_notification("ASR Result", "No text transcribed.", icon_name_cfg_key='error')
         return

    if method == 'clipboard':
        try:
            pyperclip.copy(text)
            logging.info("Text copied to clipboard.")
            send_notification("ASR Result", "Text Copied", icon_name_cfg_key='success') # Use a success icon
        except Exception as e:
            logging.error(f"Failed to copy text to clipboard: {e}")
            send_notification("Output Error", f"Failed to copy: {e}", icon_name_cfg_key='error')

    elif method == 'type':
        if not pynput_available:
             logging.error("Cannot type text: pynput library not installed.")
             send_notification("Output Error", "pynput not installed for typing.", icon_name_cfg_key='error')
             return
        try:
            # Add a small delay to allow user to focus the target window
            time.sleep(0.2)
            keyboard = KeyboardController()
            # Type character by character can be more reliable than pasting
            # Adjust delay if needed
            for char in text:
                 keyboard.type(char)
                 time.sleep(0.005) # Small delay between chars
            logging.info("Text typed out.")
            # Notification might be redundant if user sees typing
            # send_notification("ASR Result", "Text Typed", icon_name_cfg_key='success')
        except Exception as e:
            logging.error(f"Failed to type text: {e}")
            send_notification("Output Error", f"Failed to type: {e}", icon_name_cfg_key='error')

    elif method == 'file':
        filepath = cfg.get_setting('Output', 'output_file')
        if not filepath:
            logging.error("Output method is 'file' but 'output_file' is not set in config.")
            send_notification("Config Error", "output_file not set for file output.", icon_name_cfg_key='error')
            return
        try:
            filepath = os.path.expanduser(filepath)
            # Ensure directory exists
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, 'a', encoding='utf-8') as f:
                # Add timestamp? Newline?
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"\n--- {timestamp} ---\n")
                f.write(text + "\n")
            logging.info(f"Text appended to file: {filepath}")
            send_notification("ASR Result", f"Text saved to {os.path.basename(filepath)}", icon_name_cfg_key='success')
        except IOError as e:
            logging.error(f"Failed to append text to file {filepath}: {e}")
            send_notification("Output Error", f"Failed to write file: {e}", icon_name_cfg_key='error')
        except Exception as e:
             logging.error(f"Unexpected error writing file: {e}")
             send_notification("Output Error", f"Error writing file: {e}", icon_name_cfg_key='error')


    else:
        logging.error(f"Unknown output method configured: {method}")
        send_notification("Config Error", f"Unknown output method: {method}", icon_name_cfg_key='error')
