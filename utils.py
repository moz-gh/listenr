import subprocess
import logging
import config_manager as cfg # Use alias for clarity

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

notify_send_available = None # Check once

def check_notify_send():
    global notify_send_available
    if notify_send_available is None:
         try:
             subprocess.run(['which', 'notify-send'], check=True, capture_output=True)
             notify_send_available = True
             logging.info("notify-send command found.")
         except (subprocess.CalledProcessError, FileNotFoundError):
             notify_send_available = False
             logging.warning("notify-send command not found. Install libnotify-bin for notifications.")
    return notify_send_available

def send_notification(summary, body="", icon_name_cfg_key='app_icon', urgency='low'):
    """Sends desktop notification if enabled and possible."""
    if not cfg.get_bool_setting('UI', 'show_notifications'):
        return

    if not check_notify_send():
        if 'suppress_notify_warning' not in globals(): # Show warning only once
             logging.warning("Cannot send notification: notify-send not available.")
             globals()['suppress_notify_warning'] = True
        return

    icon_name = cfg.get_setting('Icons', icon_name_cfg_key)

    cmd = [
        'notify-send',
        '--app-name', cfg.APP_NAME,
        '--icon', icon_name,
        '--urgency', urgency, # low, normal, critical
        summary
    ]
    if body:
        cmd.append(body)

    try:
        subprocess.run(cmd, check=True, timeout=2)
    except FileNotFoundError:
         # Should be caught by check_notify_send, but as fallback
         logging.error("notify-send command unexpectedly not found during execution.")
         notify_send_available = False # Mark as unavailable
    except subprocess.TimeoutExpired:
         logging.warning("notify-send command timed out.")
    except subprocess.CalledProcessError as e:
         logging.error(f"notify-send failed: {e}")
    except Exception as e:
         logging.error(f"Unexpected error sending notification: {e}")

# Initialize check
check_notify_send()