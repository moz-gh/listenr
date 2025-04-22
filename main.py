#!/usr/bin/env python
import time
import os
import sys
import signal # For handling termination signals
import logging
import queue
import threading

# --- Watchdog for file signals ---
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileCreatedEvent
    watchdog_available = True
except ImportError:
    print("Warning: 'watchdog' library not found. Falling back to polling.", file=sys.stderr)
    print("Install it for better performance: pip install watchdog", file=sys.stderr)
    watchdog_available = False

# --- GTK / GLib for main loop and scheduling ---
try:
    import gi
    gi.require_version('Gtk', '3.0')
    # AppIndicator optional now, but keep GLib for idle_add
    # gi.require_version('AppIndicator3', '0.1')
    # from gi.repository import Gtk, AppIndicator3, GLib
    from gi.repository import GLib
    gi_available = True
except ImportError:
    print("Warning: PyGObject (gi) not found. Cannot use GLib main loop/idle_add.", file=sys.stderr)
    print("The service might block unexpectedly without it.", file=sys.stderr)
    gi_available = False # Script might not function correctly

# --- Project Modules ---
import config_manager as cfg
from utils import send_notification, logger, set_notify_send_available, run_command # Use shared logger
from audio_recorder import AudioRecorder
from asr_processor import WhisperProcessor
from output_handler import handle_output

# --- Global State ---
recorder = None
processor = None
keep_running = True # Flag to control main loop and threads
asr_active = False # Is the ASR processing currently active/unpaused?
transcription_queue = queue.Queue() # Queue for audio segments from recorder
observer = None # Watchdog observer thread

signal_dir = cfg.get_temp_audio_dir() # Use configured temp dir for signals too
start_signal_file = os.path.join(signal_dir, "asr_start_signal")
stop_signal_file = os.path.join(signal_dir, "asr_stop_signal") # This now means PAUSE
pid_file = os.path.join(signal_dir, "asr_service.pid")
logger.info(f"Using signal directory: {signal_dir}") # Add this line
try:
     with open(pid_file, 'w') as f: # pid_file uses signal_dir
         f.write(str(os.getpid()))
     logger.info(f"PID file created at {pid_file}") # Add confirmation log
except IOError as e:
     logger.warning(f"Could not write PID file {pid_file}: {e}")
# --- File Signal Event Handler (Watchdog) ---
class SignalHandler(FileSystemEventHandler):
    def on_created(self, event):
        # Use isinstance check for reliability
        if isinstance(event, FileCreatedEvent):
            logger.debug(f"Watchdog detected creation: {event.src_path}")
            if event.src_path == start_signal_file:
                logger.info("Start/Resume signal file detected.")
                # Schedule action in GLib main loop if possible
                if gi_available: GLib.idle_add(handle_start_resume_signal)
                else: handle_start_resume_signal() # Run directly if no GLib
                safe_remove(start_signal_file)
            elif event.src_path == stop_signal_file:
                logger.info("Stop/Pause signal file detected.")
                if gi_available: GLib.idle_add(handle_pause_signal)
                else: handle_pause_signal() # Run directly if no GLib
                safe_remove(stop_signal_file)

def safe_remove(filepath):
    try:
        if os.path.exists(filepath):
             os.remove(filepath)
             logger.debug(f"Removed signal file: {filepath}")
    except OSError as e:
        logger.error(f"Error removing signal file {filepath}: {e}")

# --- Action Handlers (Called by Signal Handler or Polling) ---
def handle_start_resume_signal():
    """Starts audio stream if not running, or sets state to active."""
    global recorder, asr_active
    if not recorder:
        logger.error("Recorder not initialized, cannot start/resume.")
        return False # Indicate failure back to GLib.idle_add

    if not recorder.is_recording_active:
        if not recorder.start_recording():
             logger.error("Failed to start audio stream.")
             send_notification("ASR Error", "Failed to start audio stream", icon_name_cfg_key='error', urgency='critical')
             asr_active = False # Ensure inactive on error
             return False
        # Stream started successfully
        asr_active = True
        logger.info("ASR Activated and Recording Stream Started.")
        send_notification("ASR Activated", icon_name_cfg_key='active') # Use 'active' icon
    elif not asr_active:
        # Stream is running, but processing was paused
        asr_active = True
        logger.info("ASR Resumed (processing activated).")
        send_notification("ASR Resumed", icon_name_cfg_key='active')
    else:
        logger.info("ASR already active.")

    return False # For GLib.idle_add

def handle_pause_signal():
    """Sets state to inactive/paused."""
    global asr_active
    if asr_active:
        asr_active = False
        logger.info("ASR Paused (processing deactivated).")
        send_notification("ASR Paused", icon_name_cfg_key='paused') # Use 'paused' icon
        # We keep the audio stream running in the background
    else:
        logger.info("ASR already paused.")

    return False # For GLib.idle_add

# --- Transcription Worker ---
def transcription_worker():
    """Thread function to process audio segments from the queue."""
    global processor, keep_running, asr_active, transcription_queue
    logger.info("Transcription worker thread started.")

    while keep_running:
        try:
            # Wait for an audio segment with a timeout
            audio_segment = transcription_queue.get(timeout=0.5)

            if not asr_active:
                logger.debug("ASR paused, skipping transcription of queued segment.")
                transcription_queue.task_done() # Mark item as processed even if skipped
                continue

            if processor:
                logger.info("Processing audio segment...")
                transcribed_text = processor.transcribe(audio_segment) # Pass numpy array directly
                if transcribed_text is not None: # Check for transcription success
                    # Schedule output handling in main thread if using GTK/GLib
                    if gi_available:
                        GLib.idle_add(handle_output, transcribed_text)
                    else:
                        handle_output(transcribed_text) # Run directly (might block if typing is slow)
                else:
                    logger.error("Transcription failed for segment.")
                    # Send error notification? (Handled in processor maybe)
            else:
                logger.error("Processor not available for transcription.")

            transcription_queue.task_done() # Signal queue that item is processed

        except queue.Empty:
            # Timeout occurred, just loop again and check keep_running
            continue
        except Exception as e:
            logger.error(f"Error in transcription worker: {e}", exc_info=True)
            # Avoid crashing the thread, maybe add a small sleep
            time.sleep(1)

    logger.info("Transcription worker thread finished.")


# --- Graceful Shutdown Handler ---
def shutdown_handler(signum, frame):
    """Handles SIGINT/SIGTERM for graceful shutdown."""
    global keep_running, recorder, observer, transcription_thread
    if not keep_running: return # Avoid multiple calls
    logger.info(f"Received signal {signum}. Shutting down...")
    keep_running = False # Signal loops to stop

    # Stop watchdog first
    if observer and observer.is_alive():
        try: observer.stop()
        except Exception as e: logger.error(f"Error stopping observer: {e}")

    # Stop audio recorder stream
    if recorder and recorder.is_recording_active:
        logger.info("Stopping audio stream...")
        recorder.stop_recording() # Should be quick

    # Wait for threads to finish (with timeout)
    if observer and observer.is_alive():
         logger.info("Waiting for observer thread...")
         observer.join(timeout=1.0)
         if observer.is_alive(): logger.warning("Observer thread did not exit.")

    # Transcription worker will exit due to keep_running flag and queue timeout

    # No GTK main loop to quit if not using AppIndicator

    logger.info("Shutdown sequence complete.")
    # Clean up PID file happens in finally block of main_service

# --- Main Service Function ---
def main_service():
    global recorder, processor, observer, keep_running, transcription_thread

    # --- Initial Setup ---
    logger.info(f"Starting {cfg.APP_NAME} Service...")
    # Write PID file
    try:
         with open(pid_file, 'w') as f: f.write(str(os.getpid()))
    except IOError as e: logger.warning(f"Could not write PID file {pid_file}: {e}")

    # Check notify-send availability (utils function sets global flag)
    set_notify_send_available(run_command(['which', 'notify-send']) is not None)

    # Initialize components
    try:
        # Queue must exist before recorder
        transcription_thread = threading.Thread(target=transcription_worker, daemon=True)

        recorder = AudioRecorder(transcription_queue) # Pass the queue
        processor = WhisperProcessor() # Loads model on init
        if processor.model is None:
             logger.critical("Failed to load Whisper model on startup. Exiting.")
             safe_remove(pid_file)
             return 1 # Exit code for failure
    except Exception as e:
        logger.critical(f"Failed during initialization: {e}", exc_info=True)
        safe_remove(pid_file)
        return 1

    # Clean up any stale signal files
    safe_remove(start_signal_file)
    safe_remove(stop_signal_file)

    # --- Start Background Threads ---
    transcription_thread.start()

    # --- Start Watchdog or Polling ---
    if watchdog_available:
        event_handler = SignalHandler()
        observer = Observer()
        try:
            observer.schedule(event_handler, signal_dir, recursive=False)
            observer.start()
            logger.info(f"File system observer started watching {signal_dir}.")
        except Exception as e:
             logger.error(f"Failed to start file system observer: {e}", exc_info=True)
             logger.error("Falling back to simple polling (less efficient).")
             observer = None # Disable observer logic
    else:
         observer = None # Watchdog not installed
         logger.warning("Using polling for signal files (install 'watchdog' for better performance).")


    # --- Main Loop ---
    # Use GLib main loop if available for better integration (e.g., idle_add)
    # Otherwise, use a simple polling loop.
    main_loop = None
    if gi_available:
        main_loop = GLib.MainLoop()
        # Add signal handlers for GLib loop quit
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, main_loop.quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, main_loop.quit)
        logger.info("Running with GLib main loop.")
        try:
            main_loop.run() # Blocks here until loop.quit()
        except KeyboardInterrupt:
            logger.info("GLib loop interrupted.") # Should be caught by signal handler too
        finally:
             if main_loop.is_running(): main_loop.quit()
             # Call shutdown handler explicitly if loop exited unexpectedly
             if keep_running: shutdown_handler(signal.SIGTERM, None)

    else: # Fallback polling loop if GLib/GI not available
        logger.info("Running with basic polling loop (GLib not available).")
        try:
            while keep_running:
                if os.path.exists(start_signal_file):
                    logger.info("(Polling) Start signal detected.")
                    handle_start_resume_signal()
                    safe_remove(start_signal_file)
                if os.path.exists(stop_signal_file):
                    logger.info("(Polling) Stop signal detected.")
                    handle_pause_signal()
                    safe_remove(stop_signal_file)
                time.sleep(0.25) # Poll interval
        except KeyboardInterrupt:
            logger.info("Polling loop interrupted.")
            # Call shutdown handler explicitly
            if keep_running: shutdown_handler(signal.SIGINT, None)
        finally:
            logger.info("Polling loop finished.")


    # --- Cleanup ---
    # Wait for transcription thread (it checks keep_running)
    if transcription_thread and transcription_thread.is_alive():
         logger.info("Waiting for transcription worker thread...")
         transcription_thread.join(timeout=2.0)
         if transcription_thread.is_alive(): logger.warning("Transcription worker did not exit.")

    safe_remove(pid_file)
    logger.info(f"{cfg.APP_NAME} Service Stopped.")
    return 0


if __name__ == "__main__":
    # Setup signal handlers for graceful shutdown *before* starting main loop
    signal.signal(signal.SIGINT, shutdown_handler) # Ctrl+C
    signal.signal(signal.SIGTERM, shutdown_handler) # systemd stop

    # Run the main service function
    exit_code = main_service()
    sys.exit(exit_code)