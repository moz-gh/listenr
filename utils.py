import subprocess
import logging
import config_manager as cfg # Use alias for clarity

# Configure logging (will be configured by main.py, but set basic here as fallback)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(cfg.APP_NAME) # Use consistent logger name

notify_send_available = False # This will be set by main.py after checking

def set_notify_send_available(available):
    """Allows main.py to set the availability status."""
    global notify_send_available
    notify_send_available = available

def send_notification(summary, body="", icon_name_cfg_key='app_icon', urgency='low'):
    """Sends desktop notification if enabled and possible."""
    if not cfg.get_bool_setting('UI', 'show_notifications'):
        return

    if not notify_send_available:
        # Log warning only once per session perhaps? Handled by main.py's initial check now.
        # logger.warning("Cannot send notification: notify-send not available.")
        return

    icon_name = cfg.get_setting('Icons', icon_name_cfg_key)

    # Build command - Removed --transient based on previous user feedback
    cmd = [
        'notify-send',
        '--app-name', cfg.APP_NAME,
        '--icon', icon_name,
        '--urgency', urgency, # low, normal, critical
        # '--transient', # Removed
        summary
    ]
    if body:
        cmd.append(body)

    try:
        # Run detached? No, short command.
        subprocess.run(cmd, check=True, timeout=2, capture_output=True) # Capture output to hide it
        logger.debug(f"Sent notification: {summary}")
    except FileNotFoundError:
        # Should not happen if check in main is done, but safety first
        logger.error("notify-send command not found during execution.")
        set_notify_send_available(False) # Mark as unavailable
    except subprocess.TimeoutExpired:
         logger.warning(f"notify-send command timed out for: {summary}")
    except subprocess.CalledProcessError as e:
         # Log stderr from notify-send if it failed
         logger.error(f"notify-send failed for '{summary}': {e.stderr.decode('utf-8', errors='ignore').strip()}")
    except Exception as e:
         logger.error(f"Unexpected error sending notification '{summary}': {e}")
         
def run_command(cmd_list):
    """Runs a command, returns stdout, handles errors."""
    try:
        # Use capture_output=True to prevent command output unless error
        result = subprocess.run(cmd_list, capture_output=True, text=True, check=True, timeout=5)
        return result.stdout.strip()
    except FileNotFoundError:
        # Log error only if it's not the initial notify-send check failing
        if not (cmd_list[0] == 'which' and cmd_list[1] == 'notify-send' and notify_send_available is False):
             logger.error(f"Command not found: {cmd_list[0]}")
        return None
    except subprocess.CalledProcessError as e:
         logger.debug(f"Command failed: {' '.join(cmd_list)} -> {e.stderr.decode('utf-8', errors='ignore').strip()}")
         return None
    except subprocess.TimeoutExpired:
         logger.warning(f"Command timed out: {' '.join(cmd_list)}")
         return None
    except Exception as e:
         logger.error(f"Unexpected error running {' '.join(cmd_list)}: {e}")
         return None