"""
Tiny HTTP server that streams GLaDOS agent state to local web clients via SSE.

Usage
-----
- Call ``StateServer().start()`` once.
- Call ``server.update(state, rms)`` whenever the agent state changes.
- Connect from the browser at  http://localhost:7860/state/stream  (SSE)
  or                           http://localhost:7860/state          (one-shot JSON).
"""

from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


class StateServer:
    """Thread-safe SSE server that broadcasts agent state changes."""

    DEFAULT_PORT = 7860

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self._port    = port
        self._lock    = threading.Lock()
        self._state: dict[str, Any] = {"state": "idle", "rms": 0.0}
        self._clients: list[queue.SimpleQueue[str]] = []
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

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def start(self) -> None:
        handler = _build_handler(self)
        self._server = HTTPServer(("0.0.0.0", self._port), handler)
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
