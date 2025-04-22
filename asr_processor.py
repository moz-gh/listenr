import logging
import time
import config_manager as cfg
from utils import send_notification

# Conditional import for type hinting if needed
try:
    from faster_whisper import WhisperModel
    faster_whisper_available = True
except ImportError:
    logging.error("faster-whisper library is required but not installed.")
    faster_whisper_available = False

class WhisperProcessor:
    def __init__(self):
        self.model = None
        self.model_size = cfg.get_setting('Whisper', 'model_size')
        self.device = cfg.get_setting('Whisper', 'device')
        self.compute_type = cfg.get_setting('Whisper', 'compute_type')
        self.beam_size = cfg.get_int_setting('Whisper', 'beam_size')
        self.load_model()

    def load_model(self):
        if not faster_whisper_available:
            logging.critical("Cannot load model, faster-whisper is not available.")
            return False

        if self.model is not None:
            logging.info("Whisper model already loaded.")
            return True

        logging.info(f"Loading Whisper model: {self.model_size} (Device: {self.device}, Compute: {self.compute_type})")
        send_notification("ASR Engine", f"Loading model: {self.model_size}...", icon_name_cfg_key='processing')
        start_time = time.time()
        try:
            self.model = WhisperModel(self.model_size,
                                      device=self.device,
                                      compute_type=self.compute_type)
                                      # Optional: download_root=cfg.get_setting('Paths','model_cache'))
            load_time = time.time() - start_time
            logging.info(f"Model loaded in {load_time:.2f} seconds.")
            # send_notification("ASR Engine", "Model Ready", icon_name_cfg_key='success') # Can be noisy
            return True
        except Exception as e:
            logging.error(f"Failed to load Whisper model: {e}")
            send_notification("ASR Engine Error", f"Failed to load model: {e}", icon_name_cfg_key='error', urgency='critical')
            self.model = None
            return False

    def transcribe(self, audio_filepath):
        if self.model is None:
            logging.error("Transcription failed: Model not loaded.")
            # Attempt to reload?
            if not self.load_model(): # Try loading again
                 return None

        logging.info(f"Starting transcription for: {audio_filepath}")
        send_notification("ASR Processing", "Transcribing audio...", icon_name_cfg_key='processing')
        start_time = time.time()
        try:
            segments, info = self.model.transcribe(audio_filepath, beam_size=self.beam_size)

            # Use generator expression for efficiency
            full_text = "".join(segment.text for segment in segments).strip()

            duration = info.duration
            transcribe_time = time.time() - start_time
            logging.info(f"Transcription complete ({duration:.2f}s audio in {transcribe_time:.2f}s). Language: {info.language}")
            return full_text

        except Exception as e:
            logging.error(f"Error during transcription: {e}")
            send_notification("ASR Error", f"Transcription failed: {e}", icon_name_cfg_key='error', urgency='normal')
            return None
