#!/usr/bin/env python
import time
import os
import sys
import signal # For handling termination signals
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import config_manager as cfg
from utils import send_notification
from audio_recorder import AudioRecorder
from asr_processor import WhisperProcessor
from output_handler import handle_output

# Configure logging (shared with utils)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(cfg.APP_NAME) # Use specific logger name

# --- Global State ---
recorder = None
processor = None
keep_running = True
signal_dir = os.environ.get('XDG_RUNTIME_DIR', '/tmp') # Consistent signal dir
start_signal_file = os.path.join(signal_dir, "asr_start_signal")
stop_signal_file = os.path.join(signal_dir, "asr_stop_signal")
pid_file = os.path.join(signal_dir, "asr_service.pid")

# --- Signal File Event Handler ---
class SignalHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        logger.debug(f"Watchdog detected creation: {event.src_path}")
        if event.src_path == start_signal_file:
            logger.info("Start signal file detected.")
            handle_start_signal()
            safe_remove(start_signal_file) # Clean up signal file
        elif event.src_path == stop_signal_file:
            logger.info("Stop signal file detected.")
            handle_stop_signal()
            safe_remove(stop_signal_file) # Clean up signal file

def safe_remove(filepath):
    try:
        if os.path.exists(filepath):
             os.remove(filepath)
             logger.debug(f"Removed signal file: {filepath}")
    except OSError as e:
        logger.error(f"Error removing signal file {filepath}: {e}")

# --- Action Handlers ---
def handle_start_signal():
    global recorder
    if recorder:
        if recorder.is_recording:
             logger.warning("Received START signal, but already recording.")
             # Optional: Stop and restart? For now, ignore.
             # recorder.stop_recording() # Stop previous one first
        # Ensure recorder exists before calling start
        if not recorder.start_recording(cfg.get_temp_audio_path()):
             logger.error("Failed to start recording.")
    else:
        logger.error("Recorder not initialized.")

def handle_stop_signal():
    global recorder, processor
    if recorder and recorder.is_recording:
        if recorder.stop_recording():
            audio_path = cfg.get_temp_audio_path()
            if os.path.exists(audio_path) and os.path.getsize(audio_path) > 1024: # Basic check > 1KB
                 if processor:
                     transcribed_text = processor.transcribe(audio_path)
                     if transcribed_text is not None: # Check for transcription success
                         handle_output(transcribed_text)
                     else:
                         logger.error("Transcription returned None.")
                         send_notification("ASR Error", "Transcription failed.", icon_name_cfg_key='error')
                 else:
                      logger.error("Processor not initialized.")
            else:
                 logger.warning(f"Audio file {audio_path} is missing or too small after recording stop.")
                 send_notification("ASR Error", "No audio data captured.", icon_name_cfg_key='error')
        else:
             logger.error("Failed to stop recording cleanly.")
    else:
        logger.warning("Received STOP signal, but not recording.")

# --- Graceful Shutdown Handler ---
def shutdown_handler(signum, frame):
    global keep_running, recorder, observer
    logger.info(f"Received signal {signum}. Shutting down...")
    keep_running = False
    if recorder and recorder.is_recording:
        logger.info("Stopping active recording...")
        recorder.stop_recording()
    if observer:
         observer.stop() # Stop watchdog observer
    # No need to join observer thread if main loop exits

# --- Main Service Function ---
def main_service():
    global recorder, processor, observer, keep_running

    # Write PID file
    try:
         with open(pid_file, 'w') as f:
             f.write(str(os.getpid()))
    except IOError as e:
         logger.warning(f"Could not write PID file {pid_file}: {e}")


    logger.info(f"Starting {cfg.APP_NAME} Service...")
    logger.info(f"Watching for signal files in: {signal_dir}")

    # Initialize components
    try:
        recorder = AudioRecorder()
        processor = WhisperProcessor() # Loads model on init
        if processor.model is None:
             logger.critical("Failed to load Whisper model on startup. Exiting.")
             return # Exit if model fails initially
    except Exception as e:
        logger.critical(f"Failed during initialization: {e}", exc_info=True)
        return

    # Clean up any stale signal files from previous runs
    safe_remove(start_signal_file)
    safe_remove(stop_signal_file)

    # Set up watchdog observer
    event_handler = SignalHandler()
    observer = Observer()
    try:
        # Watch the directory where signal files are created
        observer.schedule(event_handler, signal_dir, recursive=False)
        observer.start()
        logger.info("File system observer started.")
    except Exception as e:
         logger.error(f"Failed to start file system observer: {e}", exc_info=True)
         logger.error("Falling back to simple polling (less efficient).")
         observer = None # Disable observer logic if setup failed

    # Main loop - either wait for observer or basic poll as fallback
    try:
        while keep_running:
            if observer and observer.is_alive():
                # Observer runs in background thread, just sleep main thread
                time.sleep(1)
            elif observer is None and keep_running: # Fallback polling
                if os.path.exists(start_signal_file):
                     logger.info("(Polling) Start signal detected.")
                     handle_start_signal()
                     safe_remove(start_signal_file)
                if os.path.exists(stop_signal_file):
                     logger.info("(Polling) Stop signal detected.")
                     handle_stop_signal()
                     safe_remove(stop_signal_file)
                time.sleep(0.25) # Poll interval
            elif not observer.is_alive() and observer is not None:
                 logger.error("Watchdog observer thread seems to have died. Exiting.")
                 keep_running = False


    except Exception as e:
        logger.error(f"Error in main loop: {e}", exc_info=True)
    finally:
        logger.info("Main loop finished.")
        if observer and observer.is_alive():
            observer.stop()
            observer.join()
            logger.info("File system observer stopped.")
        # Clean up PID file
        safe_remove(pid_file)


if __name__ == "__main__":
    # Setup signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, shutdown_handler) # Ctrl+C
    signal.signal(signal.SIGTERM, shutdown_handler) # systemd stop

    main_service()
