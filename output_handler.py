import pyperclip
import logging
import os
import time
import re # Import regex
import subprocess # To run shell commands
import shlex # To parse shell commands safely
from datetime import datetime # For new note filenames
import config_manager as cfg
from utils import send_notification, logger, run_command # Import run_command

# Conditional import for pynput (remains the same)
try:
    from pynput.keyboard import Controller as KeyboardController
    pynput_available = True
except ImportError:
    logger.warning("pynput library not found. 'type' output method will not work.")
    pynput_available = False

# --- Command Processing Logic ---

def load_commands_from_config():
    """Loads command definitions from the [Commands] section."""
    commands = []
    if cfg.config.has_section('Commands'):
        for trigger_name, value in cfg.config.items('Commands'):
            try:
                # Split pattern and action using ':::' as separator
                pattern_str, action_str = value.split(':::', 1)
                pattern_str = pattern_str.strip()
                action_str = action_str.strip()

                # Compile regex (case-insensitive)
                pattern = re.compile(pattern_str, re.IGNORECASE)

                commands.append({'name': trigger_name, 'regex': pattern, 'action': action_str})
                logger.info(f"Loaded command '{trigger_name}': Pattern='{pattern_str}', Action='{action_str}'")
            except ValueError:
                logger.error(f"Invalid format for command '{trigger_name}' in config. Skipping. Use 'regex ::: action'.")
            except re.error as e:
                 logger.error(f"Invalid regex for command '{trigger_name}': {e}. Skipping.")
    return commands

# Load commands when module is loaded
COMMANDS = load_commands_from_config()

def execute_command_action(action_str, match_groups, full_text):
    """Executes the action defined for a matched command."""
    logger.info(f"Executing action: {action_str}")
    success = False
    message = f"Action '{action_str}' triggered." # Default success message

    try:
        # --- Special Actions ---
        if action_str.startswith('NEW_NOTE::'):
            directory = os.path.expanduser(action_str.split('::', 1)[1])
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"note_{timestamp}.md" # Or .txt
            filepath = os.path.join(directory, filename)
            try:
                os.makedirs(directory, exist_ok=True)
                with open(filepath, 'w', encoding='utf-8') as f:
                    # Optionally add matched text or leave blank? Add placeholder.
                    note_content = match_groups[0] if match_groups else "New note created via voice."
                    f.write(f"# Note - {timestamp}\n\n")
                    f.write(note_content + "\n") # Write captured group if exists
                logger.info(f"Created new note: {filepath}")
                message = f"New note created: {filename}"
                success = True
            except IndexError: # No capture group for content
                 with open(filepath, 'w', encoding='utf-8') as f:
                      f.write(f"# Note - {timestamp}\n\n")
                 logger.info(f"Created new empty note: {filepath}")
                 message = f"New empty note created: {filename}"
                 success = True
            except IOError as e:
                logger.error(f"Failed to create new note {filepath}: {e}")
                message = f"Error creating note: {e}"
            except Exception as e:
                 logger.error(f"Unexpected error creating note: {e}")
                 message = f"Error creating note: {e}"


        elif action_str.startswith('APPEND_NOTE::'):
            filepath = os.path.expanduser(action_str.split('::', 1)[1])
            if not match_groups:
                logger.error(f"APPEND_NOTE action requires a capture group in regex for content.")
                message = "Error: APPEND_NOTE needs text."
            else:
                content_to_append = match_groups[0].strip()
                try:
                    os.makedirs(os.path.dirname(filepath), exist_ok=True)
                    with open(filepath, 'a', encoding='utf-8') as f:
                        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                        f.write(f"[{timestamp}] {content_to_append}\n")
                    logger.info(f"Appended to note: {filepath}")
                    message = f"Note appended to {os.path.basename(filepath)}"
                    success = True
                except IOError as e:
                    logger.error(f"Failed to append to note {filepath}: {e}")
                    message = f"Error appending note: {e}"
                except Exception as e:
                    logger.error(f"Unexpected error appending note: {e}")
                    message = f"Error appending note: {e}"

        # --- Shell Command Action ---
        else:
            # Format the command string with captured groups
            try:
                formatted_action = action_str.format(*match_groups)
            except IndexError:
                logger.error(f"Regex for action '{action_str}' matched, but has fewer capture groups than format specifiers. Using full text.")
                # Fallback: maybe use the whole text if formatting fails? Risky.
                # Or just run the command without arguments if format fails.
                formatted_action = action_str # Run command as is

            logger.info(f"Running shell command: {formatted_action}")
            try:
                # Use subprocess.Popen for non-blocking execution
                # Use shlex.split to handle arguments/quotes safely IF the command isn't complex shell syntax
                # For complex shell syntax (pipes, redirects), use shell=True WITH CAUTION
                # Simple case (command and args):
                # args = shlex.split(formatted_action)
                # process = subprocess.Popen(args)

                # Safer for general commands, allows basic shell features if needed, but less safe:
                process = subprocess.Popen(formatted_action, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                # We don't wait for completion here, just launch it
                logger.info(f"Launched command in background (PID: {process.pid})")
                message = f"Command '{formatted_action}' launched."
                success = True

            except Exception as e:
                logger.error(f"Failed to run shell command '{formatted_action}': {e}")
                message = f"Error running command: {e}"

    except Exception as e:
        logger.error(f"Error processing action string '{action_str}': {e}")
        message = "Internal error processing action."

    # Send notification about the action result
    send_notification(
        "Command Executed" if success else "Command Error",
        message,
        icon_name_cfg_key='success' if success else 'error',
        urgency='normal' if success else 'critical'
    )
    return success


# --- Main Handler ---
def handle_output(text):
    """Checks text for commands, executes if match, otherwise uses default output method."""
    if not text:
         logger.warning("No text received to handle.")
         # send_notification("ASR Result", "No text transcribed.", icon_name_cfg_key='error') # Already handled?
         return

    logger.info(f"Handling text: '{text}'")

    # --- Check Commands ---
    command_executed = False
    for command in COMMANDS:
        match = command['regex'].fullmatch(text.strip()) # Use fullmatch for stricter matching
        if match:
            logger.info(f"Matched command '{command['name']}'")
            command_executed = execute_command_action(command['action'], match.groups(), text.strip())
            # Stop after the first match
            break

    # --- Default Action if No Command Matched ---
    if not command_executed:
        logger.info("No command matched, using default output method.")
        default_method = cfg.get_setting('Output', 'method').lower()
        if default_method == 'clipboard':
            try:
                pyperclip.copy(text)
                logger.info("Text copied to clipboard.")
                send_notification("ASR Result", "Text Copied", icon_name_cfg_key='success')
            except Exception as e:
                # Log full error using exc_info
                logger.error(f"Failed to copy text to clipboard: {e}", exc_info=True)
                # Attempt to provide helpful info in notification
                err_msg = str(e)
                if "xclip" in err_msg or "mechanism" in err_msg:
                     err_msg = "Clipboard tool (xclip?) not found or failed."
                send_notification("Output Error", f"Failed to copy: {err_msg}", icon_name_cfg_key='error')

        elif default_method == 'type':
            if not pynput_available:
                 logger.error("Cannot type text: pynput library not installed.")
                 send_notification("Output Error", "pynput not installed for typing.", icon_name_cfg_key='error')
                 return
            try:
                logger.info("Typing text...")
                time.sleep(0.2) # Delay to focus window
                keyboard = KeyboardController()
                for char in text:
                     keyboard.type(char)
                     time.sleep(0.005)
                logger.info("Finished typing text.")
            except Exception as e:
                logger.error(f"Failed to type text: {e}", exc_info=True)
                send_notification("Output Error", f"Failed to type: {e}", icon_name_cfg_key='error')

        elif default_method == 'file':
            filepath = cfg.get_setting('Output', 'output_file')
            if not filepath:
                logger.error("Default method is 'file' but 'output_file' is not set.")
                send_notification("Config Error", "output_file not set for file method.", icon_name_cfg_key='error')
                return
            try:
                filepath = os.path.expanduser(filepath)
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                with open(filepath, 'a', encoding='utf-8') as f:
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"\n--- {timestamp} ---\n")
                    f.write(text + "\n")
                logger.info(f"Text appended to file: {filepath}")
                send_notification("ASR Result", f"Text saved to {os.path.basename(filepath)}", icon_name_cfg_key='success')
            except Exception as e:
                logger.error(f"Failed to append text to file {filepath}: {e}", exc_info=True)
                send_notification("Output Error", f"Failed to write file: {e}", icon_name_cfg_key='error')

        else:
            logger.error(f"Unknown default output method configured: {default_method}")
            send_notification("Config Error", f"Unknown output method: {default_method}", icon_name_cfg_key='error')
