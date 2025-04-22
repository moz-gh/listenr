import sounddevice as sd
import soundfile as sf
import threading
import queue
import logging
import time
import config_manager as cfg
from utils import send_notification

class AudioRecorder:
    def __init__(self):
        self.recording_thread = None
        self.stop_event = threading.Event()
        self.audio_queue = queue.Queue()
        self.is_recording = False
        self.output_filepath = None
        self.samplerate = cfg.get_int_setting('Audio', 'sample_rate')
        self.channels = cfg.get_int_setting('Audio', 'channels')
        self.device = cfg.get_setting('Audio', 'input_device')
        if self.device.lower() == 'default' or not self.device:
            self.device = None # sounddevice uses default if None

    def _recording_worker(self):
        """Worker thread function to capture audio."""
        logging.info(f"Recording started. Output: {self.output_filepath}")
        send_notification("Recording Started", icon_name_cfg_key='recording')
        try:
            # Use sounddevice stream context manager
            with sf.SoundFile(self.output_filepath, mode='w', samplerate=self.samplerate,
                              channels=self.channels, subtype='PCM_16') as file: # WAV, 16-bit PCM
                with sd.InputStream(samplerate=self.samplerate, device=self.device,
                                    channels=self.channels, callback=self._audio_callback,
                                    blocksize=1024): # Adjust blocksize if needed
                    # Wait until stop_event is set
                    while not self.stop_event.is_set():
                        try:
                            # Process audio data written by callback
                            file.write(self.audio_queue.get(timeout=0.1)) # Timeout avoids busy-wait
                        except queue.Empty:
                            continue # No data yet, check stop_event again
                    logging.info("Stop event received.")
                    # Process remaining items in queue after stop signal
                    while not self.audio_queue.empty():
                         try: file.write(self.audio_queue.get_nowait())
                         except queue.Empty: break

        except sd.PortAudioError as e:
             logging.error(f"PortAudio error during recording: {e}")
             send_notification("Recording Error", f"Audio device issue: {e}", icon_name_cfg_key='error', urgency='critical')
             # Signal main thread about error?
        except Exception as e:
            logging.error(f"Error during recording to {self.output_filepath}: {e}")
            send_notification("Recording Error", str(e), icon_name_cfg_key='error', urgency='critical')
        finally:
            logging.info("Recording thread finished.")
            self.is_recording = False # Ensure state is updated

    def _audio_callback(self, indata, frames, time, status):
        """This is called (from a separate thread) for each audio block."""
        if status:
            logging.warning(f"Audio callback status: {status}")
        # Add audio data to the queue to be written by the worker thread
        self.audio_queue.put(indata.copy())

    def start_recording(self, filepath):
        if self.is_recording:
            logging.warning("Already recording. Stop previous recording first.")
            return False

        self.output_filepath = filepath
        self.stop_event.clear()
        # Ensure queue is empty before starting
        while not self.audio_queue.empty():
            try: self.audio_queue.get_nowait()
            except queue.Empty: break

        try:
             # Check if device is valid before starting thread
             if self.device: sd.check_input_settings(device=self.device, channels=self.channels)
             else: sd.check_input_settings(channels=self.channels) # Check default

             self.recording_thread = threading.Thread(target=self._recording_worker)
             self.is_recording = True
             self.recording_thread.start()
             return True
        except ValueError as e:
             logging.error(f"Invalid audio device setting ('{self.device}'): {e}")
             send_notification("Audio Device Error", str(e), icon_name_cfg_key='error', urgency='critical')
             return False
        except sd.PortAudioError as e:
             logging.error(f"Cannot start recording, PortAudioError: {e}")
             send_notification("Audio Device Error", str(e), icon_name_cfg_key='error', urgency='critical')
             return False


    def stop_recording(self):
        if not self.is_recording or not self.recording_thread:
            logging.warning("Not recording.")
            return False

        logging.info("Attempting to stop recording...")
        self.stop_event.set()
        self.recording_thread.join(timeout=2.0) # Wait for thread to finish

        if self.recording_thread.is_alive():
             logging.warning("Recording thread did not stop gracefully.")
             # Force stop? This might corrupt the file.
             # For now, just log it.

        self.is_recording = False
        self.recording_thread = None
        logging.info("Recording stopped.")
        # Notification is sent by the caller (main.py) after processing starts
        return True