import sounddevice as sd
import soundfile as sf
import numpy as np
import torch
import threading
import queue
import logging
import time
import os
import collections # For deque
import config_manager as cfg
from utils import send_notification, logger # Use the shared logger

# --- Silero VAD Setup ---
try:
    # VAD Model downloaded automatically by torch.hub
    vad_model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                                      model='silero_vad',
                                      force_reload=False, # Set to True to redownload model
                                      onnx=True) # Use ONNX for potentially better CPU performance
    (get_speech_timestamps,
     save_audio,
     read_audio,
     VADIterator,
     collect_chunks) = utils
    # Pass threshold directly if needed later: VADIterator(vad_model, threshold=cfg.get_float_setting('VAD', 'speech_threshold'))
    vad_iterator = VADIterator(vad_model)
    silero_vad_available = True
    logger.info("Silero VAD model loaded successfully.")
except Exception as e:
    logger.error(f"Failed to load Silero VAD model: {e}", exc_info=True)
    logger.error("VAD-based streaming will not work. Install requirements.")
    silero_vad_available = False
    vad_iterator = None

class AudioRecorder:
    def __init__(self, transcription_queue):
        if not silero_vad_available:
            raise RuntimeError("Silero VAD not available. Cannot initialize AudioRecorder.")

        self.transcription_queue = transcription_queue # Queue to put final speech segments
        self.stream = None
        self.stop_event = threading.Event()
        self.is_recording_active = False # Different from main 'asr_active' state

        # --- Audio Config ---
        self.samplerate = cfg.get_int_setting('Audio', 'sample_rate')
        self.channels = cfg.get_int_setting('Audio', 'channels')
        self.device = cfg.get_setting('Audio', 'input_device')
        if self.device.lower() == 'default' or not self.device:
            self.device = None # sounddevice uses default if None

        # --- VAD Config ---
        self.vad_threshold = cfg.get_float_setting('VAD', 'speech_threshold')
        self.vad_frame_ms = cfg.get_int_setting('VAD', 'frame_ms')
        # Calculate frame size based on VAD expectation and sample rate
        self.vad_frame_size = int(self.samplerate * self.vad_frame_ms / 1000)
        self.min_silence_duration_ms = cfg.get_int_setting('VAD', 'silence_duration_ms')
        self.min_speech_duration_ms = 100 # Minimum speech chunk to consider valid (tune if needed)
        self.padding_ms = int(cfg.get_float_setting('Audio', 'trailing_silence_s', allow_zero=True) * 1000) # Trailing padding
        self.leading_padding_ms = int(cfg.get_float_setting('Audio', 'leading_silence_s', allow_zero=True) * 1000) # Leading padding

        # --- Internal State ---
        self._buffer = collections.deque()
        # Adjust buffer size calculation slightly based on frame size for safety
        self._buffer_frames_count = int((self.leading_padding_ms + self.min_silence_duration_ms + 500) * self.samplerate / 1000)
        self._max_buffer_items = (self._buffer_frames_count + self.vad_frame_size - 1) // self.vad_frame_size

        self._current_speech_segment_frames = [] # Store numpy frames directly
        self._is_speaking = False
        self._silence_start_time = None

        if vad_iterator:
            vad_iterator.reset_states() # Ensure clean state

        # --- Debug: Check Devices ---
        try:
            logger.debug(f"Available audio devices:\n{sd.query_devices()}")
            if self.device: sd.check_input_settings(device=self.device, channels=self.channels, samplerate=self.samplerate)
            else: sd.check_input_settings(channels=self.channels, samplerate=self.samplerate)
            logger.info(f"Audio settings check OK (Device: {self.device or 'Default'}, Rate: {self.samplerate}, Channels: {self.channels}, VAD Frame Size: {self.vad_frame_size})")
        except Exception as e:
            logger.error(f"Audio device check failed: {e}", exc_info=True)
            raise RuntimeError(f"Audio device configuration error: {e}")


    def _audio_callback(self, indata, frames, time, status):
        """Callback for sounddevice InputStream."""
        if status:
            logger.warning(f"Audio callback status: {status}")

        if indata.dtype != np.float32:
            try: indata = indata.astype(np.float32)
            except Exception as e: logger.error(f"Failed audio dtype conversion: {e}"); return

        if self.channels > 1: mono_data = indata[:, 0].copy()
        else: mono_data = indata.flatten().copy()

        # Always add to the main buffer for context/padding
        self._buffer.append(mono_data)
        while len(self._buffer) > self._max_buffer_items: self._buffer.popleft()

        # --- VAD Processing ---
        try:
            if len(mono_data) != self.vad_frame_size:
                logger.warning(f"Unexpected audio frame size: {len(mono_data)}, expected {self.vad_frame_size}. Skipping VAD.")
                return

            audio_chunk_tensor = torch.from_numpy(mono_data).float()

            if vad_iterator is None: logger.error("vad_iterator object is None!"); return

            # --- Call VAD and Check Return Type ---
            vad_result = vad_iterator(audio_chunk_tensor, return_seconds=True) # Use return_seconds=True for timestamps

            # --- Handle VAD Result (Start/End Timestamps or None) ---
            if isinstance(vad_result, dict):
                if 'start' in vad_result:
                    start_time = vad_result['start']
                    logger.debug(f"VAD detected speech start at {start_time:.2f}s")
                    if not self._is_speaking:
                        self._is_speaking = True
                        # Add leading padding from buffer *before* the current chunk
                        self._current_speech_segment_frames = [] # Clear previous segment just in case
                        if self.leading_padding_ms > 0:
                            padding_frames = int(self.leading_padding_ms * self.samplerate / 1000)
                            num_buffer_items_needed = (padding_frames + self.vad_frame_size -1) // self.vad_frame_size
                            # Grab items from buffer *before* the current frame (index -1)
                            padding_data_items = list(self._buffer)[max(0, len(self._buffer) - num_buffer_items_needed - 1) : -1]
                            if padding_data_items:
                                self._current_speech_segment_frames.extend(padding_data_items) # Use extend
                                logger.debug(f"Prepended {len(padding_data_items)} frames of leading padding.")
                    # Append current frame since speech has started
                    self._current_speech_segment_frames.append(mono_data)

                elif 'end' in vad_result:
                    end_time = vad_result['end']
                    logger.debug(f"VAD detected speech end at {end_time:.2f}s")
                    if self._is_speaking:
                        self._is_speaking = False
                        # Current frame might be silence, but add it for trailing padding context maybe? Or not? Let's add it.
                        self._current_speech_segment_frames.append(mono_data)

                        # --- Finalize and Queue the Segment ---
                        if not self._current_speech_segment_frames:
                            logger.debug("No speech frames collected despite end event.")
                        else:
                            segment_data_np = np.concatenate(self._current_speech_segment_frames)
                            segment_duration_ms = len(segment_data_np) * 1000 / self.samplerate

                            # Check minimum duration? VAD timestamps might already handle this. Let's check anyway.
                            if segment_duration_ms >= self.min_speech_duration_ms:
                                logger.info(f"Queueing speech segment ({segment_duration_ms:.0f}ms).")
                                self.transcription_queue.put(segment_data_np)
                            else:
                                logger.debug(f"Discarding short speech segment detected by VAD ({segment_duration_ms:.0f}ms).")

                        # Reset for next utterance
                        self._current_speech_segment_frames = []
                        if vad_iterator: vad_iterator.reset_states() # Reset VAD internal state after segment
                    else:
                         # Got an 'end' without being in 'speaking' state? Log warning.
                         logger.warning("VAD reported 'end' but wasn't in speaking state.")

                else:
                    logger.error(f"VAD returned unexpected dict: {vad_result}")

            elif vad_result is None:
                # Silence within this frame
                if self._is_speaking:
                    # Continue accumulating frames if we are in a speech segment
                    self._current_speech_segment_frames.append(mono_data)
                # else: Silence continues, do nothing except buffer append (done above)

            else:
                 logger.error(f"VAD returned unexpected type: {type(vad_result)}")

        except Exception as e:
            logger.error(f"Error during VAD processing in callback: {e}", exc_info=True)

    def start_recording(self):
        if self.is_recording_active:
            logger.warning("Stream already active.")
            return False

        logger.info("Starting audio stream for VAD...")
        self.stop_event.clear()
        if vad_iterator: vad_iterator.reset_states()
        self._current_speech_segment_frames = []
        self._is_speaking = False
        self._silence_start_time = None
        self._buffer.clear()

        try:
            self.stream = sd.InputStream(
                samplerate=self.samplerate,
                device=self.device,
                channels=self.channels,
                callback=self._audio_callback,
                blocksize=self.vad_frame_size, # Crucial
                dtype='float32' # Must match VAD expectation
            )
            self.stream.start()
            self.is_recording_active = True
            logger.info("Audio stream started.")
            return True
        except sd.PortAudioError as e:
             logger.error(f"PortAudio error starting stream: {e}", exc_info=True)
             send_notification("Audio Error", f"Failed to start audio stream: {e}", icon_name_cfg_key='error', urgency='critical')
             return False
        except ValueError as e:
            logger.error(f"ValueError starting stream: {e}", exc_info=True)
            send_notification("Audio Error", f"Invalid audio setting: {e}", icon_name_cfg_key='error', urgency='critical')
            return False
        except Exception as e:
             logger.error(f"Unexpected error starting stream: {e}", exc_info=True)
             send_notification("Audio Error", f"Failed to start stream: {e}", icon_name_cfg_key='error', urgency='critical')
             return False

    def stop_recording(self):
        """Stops the sounddevice stream."""
        if not self.is_recording_active or not self.stream:
            logger.warning("Stream not active.")
            return False

        logger.info("Stopping audio stream...")
        # No explicit stop_event needed for stream, just stop/close it
        try:
            self.stream.stop()
            self.stream.close()
            logger.info("Audio stream stopped and closed.")
        except sd.PortAudioError as e:
             logger.error(f"PortAudio error stopping stream: {e}", exc_info=True)
        except Exception as e:
             logger.error(f"Unexpected error stopping stream: {e}", exc_info=True)
        finally:
             self.stream = None
             self.is_recording_active = False
             # Process any potentially remaining data? The callback should handle queueing
             # based on silence detection before the stream actually stops.
        return True