"""
Tiny HTTP server that streams GLaDOS agent state and TTS audio to local web clients via SSE.

Usage
-----
- Call ``StateServer().start()`` once, then ``set_server(srv)`` to register the singleton.
- Call ``server.update(state, rms)`` whenever the agent state changes.
- Call ``server.broadcast_audio(audio_array, sample_rate)`` to stream a TTS clip.
- Connect from the browser at  http://localhost:7860/state/stream  (state SSE)
                               http://localhost:7860/audio/stream  (audio SSE)
  or                           http://localhost:7860/state          (one-shot JSON).
"""

from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any

# ── module-level singleton ─────────────────────────────────────────────────────
_server: StateServer | None = None


def get_server() -> StateServer | None:
    """Return the active StateServer singleton, or None if not yet started."""
    return _server


def set_server(s: StateServer | None) -> None:
    """Register (or clear) the module-level singleton."""
    global _server
    _server = s


class StateServer:
    """Thread-safe SSE server that broadcasts agent state changes and TTS audio."""

    DEFAULT_PORT = 7860

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self._port    = port
        self._lock    = threading.Lock()
        self._state: dict[str, Any] = {"state": "idle", "rms": 0.0}
        self._clients: list[queue.SimpleQueue[str]] = []
        self._audio_clients: list[queue.SimpleQueue[str]] = []
        self._server: HTTPServer | None = None

    # ── public API ────────────────────────────────────────────────────────────

    def update(self, state: str, rms: float = 0.0) -> None:
        """Push a new state snapshot.  Thread-safe; called from TUI event loop."""
        # Quantise rms to 2 decimal places so micro-fluctuations don't spam clients
        payload: dict[str, Any] = {"state": state, "rms": round(float(rms), 2)}
        with self._lock:
            if payload == self._state:
                return  # no change – skip broadcasting
            self._state = payload
            clients = list(self._clients)

        msg = f"data: {json.dumps(payload)}\n\n"
        for q in clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass

    def broadcast_audio(self, audio: Any, sample_rate: int) -> None:
        """Convert a numpy float32 audio array to WAV and push to audio SSE clients."""
        import logging as _logging
        _log = _logging.getLogger(__name__)

        with self._lock:
            audio_clients = list(self._audio_clients)
        if not audio_clients:
            _log.debug("broadcast_audio: no web audio clients connected, skipping")
            return
        try:
            import base64
            import io as _io

            import numpy as np
            import soundfile as sf

            arr = np.asarray(audio, dtype=np.float32)
            buf = _io.BytesIO()
            sf.write(buf, arr, sample_rate, format="WAV", subtype="PCM_16")
            wav_bytes = buf.getvalue()
            wav_b64 = base64.b64encode(wav_bytes).decode("ascii")
            payload = json.dumps({"sampleRate": sample_rate, "wav": wav_b64})
            msg = f"data: {payload}\n\n"
            _log.info(
                "broadcast_audio: sending %.2fs of audio (%d bytes WAV, %d b64) to %d client(s)",
                len(arr) / sample_rate,
                len(wav_bytes),
                len(wav_b64),
                len(audio_clients),
            )
        except Exception as exc:
            _log.error("broadcast_audio encode error: %s", exc, exc_info=True)
            return

        for q in audio_clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass

    def broadcast_abort(self) -> None:
        """Tell all audio SSE clients to stop playback immediately."""
        with self._lock:
            audio_clients = list(self._audio_clients)
        if not audio_clients:
            return
        payload = json.dumps({"action": "abort"})
        msg = f"data: {payload}\n\n"
        for q in audio_clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def start(self) -> None:
        handler = _build_handler(self)
        # Each SSE connection blocks its thread, so we need one thread per request.
        class _ThreadedServer(ThreadingMixIn, HTTPServer):
            daemon_threads = True

        self._server = _ThreadedServer(("0.0.0.0", self._port), handler)
        t = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="glados-state-server",
        )
        t.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _add_client(self) -> queue.SimpleQueue[str]:
        q: queue.SimpleQueue[str] = queue.SimpleQueue()
        with self._lock:
            self._clients.append(q)
        return q

    def _remove_client(self, q: queue.SimpleQueue[str]) -> None:
        with self._lock:
            try:
                self._clients.remove(q)
            except ValueError:
                pass

    def _add_audio_client(self) -> queue.SimpleQueue[str]:
        q: queue.SimpleQueue[str] = queue.SimpleQueue()
        with self._lock:
            self._audio_clients.append(q)
        return q

    def _remove_audio_client(self, q: queue.SimpleQueue[str]) -> None:
        with self._lock:
            try:
                self._audio_clients.remove(q)
            except ValueError:
                pass


def _build_handler(server: StateServer) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/state":
                body = json.dumps(server.get_state()).encode()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            elif self.path == "/state/stream":
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type",  "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection",    "keep-alive")
                self.end_headers()

                q = server._add_client()
                try:
                    # Send current state immediately so the client doesn't wait
                    init = f"data: {json.dumps(server.get_state())}\n\n"
                    self.wfile.write(init.encode())
                    self.wfile.flush()

                    while True:
                        try:
                            msg = q.get(timeout=15.0)
                        except queue.Empty:
                            # heartbeat keeps the connection alive through proxies
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                            continue
                        self.wfile.write(msg.encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    server._remove_client(q)

            elif self.path == "/audio/stream":
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type",  "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection",    "keep-alive")
                self.end_headers()

                q = server._add_audio_client()
                try:
                    while True:
                        try:
                            msg = q.get(timeout=30.0)
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                            continue
                        self.wfile.write(msg.encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    server._remove_audio_client(q)

            else:
                self.send_response(404)
                self._cors()
                self.end_headers()

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin",  "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def log_message(self, fmt: str, *args: object) -> None:  # noqa: ARG002
            pass  # silence per-request logs

    return _Handler
