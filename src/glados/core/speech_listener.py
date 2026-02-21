"""
Speech listener module for the Glados voice assistant.

This module provides the SpeechListener class that handles audio input streaming,
voice activity detection, speech recognition, and wake word detection.
"""

from collections import deque
import queue
import threading
import time
from typing import Any

from Levenshtein import distance
from loguru import logger
import numpy as np
from numpy.typing import NDArray

from typing import Callable

from ..ASR import TranscriberProtocol
from ..audio_io import AudioProtocol
from .audio_state import AudioState
from ..observability import ObservabilityBus, trim_message

# Callback signature: (event_type: str) -> None
InterruptCallback = Callable[[str], None]


class SpeechListener:
    """
    Manages audio input and speech processing for a voice assistant.

    This class handles capturing audio, performing Voice Activity Detection (VAD),
    buffering pre-activation audio, triggering Automatic Speech Recognition (ASR),
    and coordinating with Language Model (LLM) and Text-to-Speech (TTS) components
    via shared events and queues. It supports optional wake word detection.
    """

    VAD_SIZE: int = 32  # Milliseconds of sample for Voice Activity Detection (VAD)
    BUFFER_SIZE: int = 800  # Milliseconds of buffer BEFORE VAD detection
    PAUSE_LIMIT: int = 640  # Milliseconds of pause allowed before processing
    SIMILARITY_THRESHOLD: int = 3  # Levenshtein edits allowed for wake word match
    HEARING_CHECK_COOLDOWN_S: float = 8.0  # Ignore repeated "can you hear me?" within this window

    # Phrases that commonly get "Yes I can hear you" — suppress repeats within cooldown
    _HEARING_CHECK_PHRASES: frozenset[str] = frozenset({
        "can you hear me", "can you hear", "are you there", "are you there?",
        "you there", "hear me", "can you hear me?",
    })

    # Stop commands: interrupt and silence, do NOT send to LLM (avoids new prompt)
    _STOP_PHRASES: frozenset[str] = frozenset({
        "stop", "stop talking", "stop talk", "stopped", "stopped talking",
        "shut up", "be quiet", "quiet", "enough", "that's enough",
        "enough already", "silence", "hush",
    })

    def __init__(
        self,
        audio_io: AudioProtocol,  # Replace with actual type if known
        llm_queue: queue.Queue[dict[str, Any]],
        shutdown_event: threading.Event,
        currently_speaking_event: threading.Event,
        processing_active_event: threading.Event,
        asr_model: TranscriberProtocol,
        wake_word: str | None,
        pause_time: float,
        interruptible: bool = True,
        interaction_state: "InteractionState | None" = None,
        observability_bus: ObservabilityBus | None = None,
        asr_muted_event: threading.Event | None = None,
        audio_state: AudioState | None = None,
        on_interrupt: InterruptCallback | None = None,
    ) -> None:
        """
        Initializes the SpeechListener with audio I/O, inter-thread communication, and ASR model.

        Args:
            audio_io: An instance conforming to `AudioProtocol` for audio input/output.
            llm_queue: A queue for sending transcribed text to the language model.
            shutdown_event: A threading.Event to signal the application to shut down.
            currently_speaking_event: A threading.Event indicating if the assistant is currently speaking.
            processing_active_event: A threading.Event indicating if processing is active (e.g., for LLM/TTS).
            asr_model: An instance conforming to `TranscriberProtocol` for speech recognition.
            wake_word: Optional wake word string to activate the assistant. Defaults to None.
            interruptible: If True, allows new speech input to interrupt ongoing assistant speech.
        """
        self.audio_io = audio_io
        self.llm_queue = llm_queue
        self.asr_model = asr_model
        self.wake_word = wake_word.lower() if wake_word else None
        self.pause_time = pause_time
        self.interruptible = interruptible

        # Cooldown to avoid repeating "yes I can hear you" for back-to-back "can you hear me?"
        self._last_hearing_check_at: float = 0.0
        # True if we started recording while assistant was speaking (for wake-word-gated interrupt)
        self._recording_during_speech: bool = False

        # Circular buffer to hold pre-activation samples
        self._buffer: deque[NDArray[np.float32]] = deque(maxlen=self.BUFFER_SIZE // self.VAD_SIZE)
        self._sample_queue = self.audio_io.get_sample_queue()

        # Internal state variables
        self._recording_started = False
        self._samples: list[NDArray[np.float32]] = []
        self._gap_counter = 0

        self.shutdown_event = shutdown_event
        self.currently_speaking_event = currently_speaking_event
        self.processing_active_event = processing_active_event
        self._interaction_state = interaction_state
        self._observability_bus = observability_bus
        self._asr_muted_event = asr_muted_event
        self._audio_state = audio_state
        self._on_interrupt = on_interrupt

    def run(self) -> None:
        """
        Starts the main listening event loop, continuously processing audio input.

        This method initializes the audio input stream and enters a loop that
        listens for incoming audio samples and their Voice Activity Detection (VAD) confidence.
        It retrieves samples from an internal queue and processes them via `_handle_audio_sample`.
        The loop runs until the `shutdown_event` is set. It also handles brief pauses
        in audio input using a timeout.

        Raises:
            Exception: Catches and logs general exceptions encountered during the listening loop,
                       without stopping the loop unless `shutdown_event` is set.
        """
        logger.success("SpeechListener ready")

        # Loop forever, but is 'paused' when new samples are not available
        try:
            while not self.shutdown_event.is_set():  # Check event BEFORE blocking get
                try:
                    # Use a timeout for the queue get
                    sample, vad_confidence = self._sample_queue.get(timeout=self.pause_time)
                    if self._asr_muted_event and self._asr_muted_event.is_set():
                        if self._recording_started or self._samples or self._buffer:
                            self.reset()
                        continue
                    self._handle_audio_sample(sample, vad_confidence)
                except queue.Empty:
                    # Timeout occurred, loop again to check shutdown_event
                    continue
                except (OSError, RuntimeError) as e:  # More specific exceptions
                    if not self.shutdown_event.is_set():  # Only log if not shutting down
                        logger.error(f"Error in listen loop ({type(e).__name__}): {e}")
                    continue

            logger.info("Shutdown event detected in listen loop, exiting loop.")

        finally:
            self.audio_io.stop_listening()
            logger.info("Listen event loop is stopping/exiting.")

        logger.info("Speech Listener thread finished.")

    def _handle_audio_sample(self, sample: NDArray[np.float32], vad_confidence: bool) -> None:
        """
        Routes the processing of an individual audio sample based on the current recording state.

        If recording has not started, the sample contributes to the pre-activation buffer.
        Once recording is active, the sample is added to the main speech segment
        and contributes to the voice activity gap detection.

        Args:
            sample: The audio sample (numpy array) to process.
            vad_confidence: True if voice activity is detected in the sample, False otherwise.
        """
        if self._audio_state is not None:
            if sample.size:
                rms = float(np.sqrt(np.mean(sample * sample)))
            else:
                rms = 0.0
            self._audio_state.update(rms, vad_confidence)
        if not self._recording_started:
            self._manage_pre_activation_buffer(sample, vad_confidence)
        else:
            self._process_activated_audio(sample, vad_confidence)

    def _manage_pre_activation_buffer(self, sample: NDArray[np.float32], vad_confidence: bool) -> None:
        """
        Manages the pre-activation circular buffer and handles voice activity detection.

        Samples are continuously added to a circular buffer until voice activity is detected.
        Upon VAD detection:
        - It checks for interruptibility if the assistant is currently speaking.
        - The assistant's speaking is stopped (`audio_io.stop_speaking()`).
        - The `processing_active_event` is cleared, pausing LLM/TTS activity.
        - The buffered samples are moved to `_samples`, and `_recording_started` is set to True.

        Args:
            sample: The current audio sample (numpy array) to be added to the buffer.
            vad_confidence: True if voice activity is detected in the sample, False otherwise.
        """
        self._buffer.append(sample)  # Automatically handles overflow

        if vad_confidence:
            if not self.interruptible and self.currently_speaking_event.is_set():
                logger.debug(f"Detected voice activity but interruptibility is disabled: {self.interruptible=}, {self.currently_speaking_event.is_set()=}")
                return

            was_speaking = self.currently_speaking_event.is_set()
            # When interruptible + wake_word: only interrupt after we confirm wake word in _process_detected_audio
            wake_word_gated = bool(self.interruptible and self.wake_word and was_speaking)

            if wake_word_gated:
                # Don't stop yet — record first; we'll interrupt in _process_detected_audio if wake word found
                self._recording_during_speech = True
                self._samples = list(self._buffer)
                self._recording_started = True
            else:
                # Immediate interrupt (no wake word, or assistant wasn't speaking)
                self.audio_io.stop_speaking()
                self._broadcast_abort()
                self.processing_active_event.clear()
                self._samples = list(self._buffer)
                self._recording_started = True

                if was_speaking and self._on_interrupt:
                    self._on_interrupt("user_interrupt")

    def _process_activated_audio(self, sample: NDArray[np.float32], vad_confidence: bool) -> None:
        """
        Accumulates audio samples and tracks pauses after voice activation.

        This method appends incoming audio samples to `self._samples`. It increments
        `_gap_counter` when no voice activity is detected. If the `_gap_counter`
        exceeds `PAUSE_LIMIT`, it signifies the end of a speech segment, triggering
        `_process_detected_audio`. Otherwise, if voice is detected, the gap counter is reset.

        Args:
            sample: A single audio sample (numpy array) from the input stream.
            vad_confidence: True if voice activity is currently detected, False otherwise.
        """
        self._samples.append(sample)

        if not vad_confidence:
            self._gap_counter += 1
            if self._gap_counter >= self.PAUSE_LIMIT // self.VAD_SIZE:
                self._process_detected_audio()
        else:
            self._gap_counter = 0

    def _wakeword_detected(self, text: str) -> bool:
        """
        Checks if the transcribed text contains a sufficiently similar match to the configured wake word.

        This method iterates through words in the `text` and calculates the Levenshtein distance
        (edit distance) between each word (converted to lowercase) and the `wake_word`.
        A match is considered found if the `closest_distance` is less than `SIMILARITY_THRESHOLD`.
        This helps account for minor misrecognitions of the wake word.

        Args:
            text: The transcribed text string to check for wake word similarity.

        Returns:
            True if a word in the text matches the wake word within the similarity threshold, False otherwise.

        Raises:
            AssertionError: If `self.wake_word` is None.
        """
        if self.wake_word is None:
            raise ValueError("Wake word should not be None")

        words = text.split()
        closest_distance = min(distance(word.lower(), self.wake_word) for word in words)
        return closest_distance <= self.SIMILARITY_THRESHOLD

    def _strip_wake_word(self, text: str) -> str:
        """Remove the wake word (and trailing punctuation/comma) from the start of *text*.

        Uses the same fuzzy matching as ``_wakeword_detected`` so that
        transcription variants like "Jarvis," or "Gervis" are all stripped.
        Returns the cleaned text, capitalising the first remaining letter.
        """
        assert self.wake_word is not None
        words = text.split()
        if not words:
            return text
        # Find the index of the wake-word token (usually 0, but be safe)
        match_idx = None
        for i, word in enumerate(words):
            stripped = word.strip(",.!?").lower()
            if distance(stripped, self.wake_word) <= self.SIMILARITY_THRESHOLD:
                match_idx = i
                break
        if match_idx is None:
            return text
        remainder = " ".join(words[match_idx + 1 :]).lstrip(",.!? ")
        return remainder[:1].upper() + remainder[1:] if remainder else text

    def _is_hearing_check(self, text: str) -> bool:
        """True if *text* is a 'can you hear me?'-style phrase that gets 'Yes I can hear you'."""
        norm = " ".join(text.lower().split()).strip(",.!?")
        if not norm:
            return False
        return any(phrase in norm or norm in phrase for phrase in self._HEARING_CHECK_PHRASES)

    def _is_stop_command(self, text: str) -> bool:
        """True if *text* is a stop command — interrupt only, do not send to LLM."""
        norm = " ".join(text.lower().split()).strip(",.!?")
        if not norm:
            return False
        return any(
            norm == phrase or norm.startswith(phrase + " ") or norm.startswith(phrase + ",")
            for phrase in self._STOP_PHRASES
        )

    def _broadcast_abort(self) -> None:
        """Tell web clients to stop playback immediately."""
        try:
            from glados.state_server import get_server  # noqa: PLC0415

            srv = get_server()
            if srv is not None:
                srv.broadcast_abort()
        except Exception:
            pass

    def reset(self) -> None:
        """
        Resets the internal state of the speech listener, clearing all audio buffers and counters.

        This prepares the listener for a new speech segment by:
        - Setting `_recording_started` to False.
        - Clearing the accumulated `_samples`.
        - Resetting the `_gap_counter`.
        - Emptying the pre-activation circular buffer (`_buffer.queue`), safely using its internal mutex.
        """
        logger.debug("Resetting recorder...")
        self._recording_started = False
        self._recording_during_speech = False
        self._samples.clear()
        self._gap_counter = 0
        self._buffer.clear()
        if self._audio_state is not None:
            self._audio_state.reset()

    def _process_detected_audio(self) -> None:
        """
        Processes the accumulated audio samples once a speech pause is detected.

        This method performs the following steps:
        1. Transcribes the collected audio samples using the ASR model.
        2. If transcription is successful:
            a. Checks for the `wake_word` (if configured).
            b. If the wake word is detected (or not required), the transcribed text is
               placed into the `llm_queue`, and `processing_active_event` is set.
        3. Resets the listener's internal state using `self.reset()`, preparing for the next input.
        """
        logger.debug("Detected pause after speech. Processing...")

        detected_text = self.asr(self._samples)

        if detected_text:
            if self.wake_word and not self._wakeword_detected(detected_text):
                # Drop silently — no logs — so echo/ambient noise leaves no trace
                pass
            else:
                # Strip the wake word (and any punctuation around it) from the
                # front of the text so the LLM receives a clean query.
                clean_text = detected_text
                if self.wake_word:
                    clean_text = self._strip_wake_word(detected_text)

                # If we recorded during assistant speech, interrupt only now (wake word confirmed)
                if self._recording_during_speech:
                    self.audio_io.stop_speaking()
                    self._broadcast_abort()
                    self.processing_active_event.clear()
                    if self._on_interrupt:
                        self._on_interrupt("user_interrupt")

                # Stop commands: interrupt and drop — do not send to LLM (avoids new response)
                if self._is_stop_command(clean_text):
                    logger.info("Stop command detected — not forwarding to LLM")
                    self.reset()
                    return

                # Suppress repeated "can you hear me?" within cooldown to avoid
                # echoing "yes I can hear you" over and over
                if self._is_hearing_check(clean_text):
                    if time.time() - self._last_hearing_check_at < self.HEARING_CHECK_COOLDOWN_S:
                        # Drop silently — already answered recently
                        self.reset()
                        return
                    self._last_hearing_check_at = time.time()

                logger.success(f"ASR text: '{clean_text}'")
                if self._observability_bus:
                    self._observability_bus.emit(
                        source="asr",
                        kind="user_input",
                        message=trim_message(clean_text),
                    )
                self.llm_queue.put(
                    {
                        "role": "user",
                        "content": clean_text,
                        "_enqueued_at": time.time(),
                        "_lane": "priority",
                    }
                )
                if self._interaction_state:
                    self._interaction_state.mark_user()
                self.processing_active_event.set()

        self.reset()

    def asr(self, samples: list[NDArray[np.float32]]) -> str:
        """
        Performs Automatic Speech Recognition (ASR) on a list of audio samples.

        The samples are first concatenated into a single audio array. This combined
        audio is then normalized to a range of [-1.0, 1.0] to ensure consistent
        volume levels before being passed to the ASR model for transcription.

        Args:
            samples: A list of numpy arrays (float32) containing audio sample chunks.

        Returns:
            The transcribed text as a string.
        """
        if not samples:
            logger.warning("ASR received empty sample list")
            return ""

        audio = np.concatenate(samples)

        # Check for silent audio
        max_abs_val = np.max(np.abs(audio))
        if max_abs_val < 1e-10:  # Threshold for effectively silent audio
            logger.warning("ASR received effectively silent audio")
            return ""

        # Normalize to full range [-1.0, 1.0]
        audio = audio / max_abs_val

        detected_text = self.asr_model.transcribe(audio)
        return detected_text
