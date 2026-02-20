"""
Thread-safe conversation history store.

This module provides a ConversationStore class that encapsulates all synchronization
for the shared conversation history, eliminating race conditions from conditional
lock usage patterns.
"""

from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any


class ConversationStore:
    """
    Thread-safe conversation history store.

    All operations are atomic and protected by internal locking.
    Consumers should NOT hold references to internal lists or mutate
    returned snapshots if they need isolation.

    This replaces the previous pattern of sharing a raw list with a
    threading.Lock that was conditionally acquired.
    """

    def __init__(
        self,
        initial_messages: list[dict[str, Any]] | None = None,
        max_messages: int | None = None,
    ) -> None:
        """
        Initialize the conversation store.

        Args:
            initial_messages: Optional initial messages (e.g., personality preprompt).
                            These are copied, not referenced.
        """
        self._lock = threading.RLock()  # RLock allows nested acquisition if needed
        self._messages: list[dict[str, Any]] = list(initial_messages or [])
        self._version: int = 0  # For change detection / optimistic concurrency
        # Optional hard cap on history length to keep prompts fast.
        # If set, we keep the most recent max_messages while preserving leading system messages.
        self._max_messages = max_messages

        if self._max_messages is not None:
            self._truncate_locked()

    def append(self, message: dict[str, Any]) -> int:
        """
        Append a single message to the conversation history.

        Args:
            message: The message dict to append (role, content, etc.)

        Returns:
            The new length of the conversation history.
        """
        with self._lock:
            self._messages.append(message)
            if self._max_messages is not None:
                self._truncate_locked()
            self._version += 1
            return len(self._messages)

    def append_multiple(self, messages: list[dict[str, Any]]) -> int:
        """
        Atomically append multiple messages to the conversation history.

        This is useful for operations that need to add several related messages
        (e.g., user message + interrupted assistant partial response).

        Args:
            messages: List of message dicts to append.

        Returns:
            The new length of the conversation history.
        """
        with self._lock:
            self._messages.extend(messages)
            if self._max_messages is not None:
                self._truncate_locked()
            self._version += 1
            return len(self._messages)

    def snapshot(self) -> list[dict[str, Any]]:
        """
        Return a shallow copy of all messages.

        The returned list is a new list object, but the message dicts
        inside are the same objects. This is safe for reading but callers
        should not mutate the individual message dicts.

        Returns:
            A shallow copy of the conversation history.
        """
        with self._lock:
            return list(self._messages)

    def deep_snapshot(self) -> list[dict[str, Any]]:
        """
        Return a deep copy of all messages for safe mutation.

        Use this when you need to modify messages without affecting
        the original store.

        Returns:
            A deep copy of the conversation history.
        """
        with self._lock:
            return deepcopy(self._messages)

    def replace_all(self, new_messages: list[dict[str, Any]]) -> None:
        """
        Atomically replace the entire conversation history.

        This is used by the compaction agent to swap in a compacted
        history without race conditions.

        Args:
            new_messages: The new message list to replace with (copied).
        """
        with self._lock:
            self._messages.clear()
            self._messages.extend(new_messages)
            if self._max_messages is not None:
                self._truncate_locked()
            self._version += 1

    def _truncate_locked(self) -> None:
        """
        Truncate history to the most recent max_messages, while keeping leading system messages.

        This keeps prompts small and latency low on local models.
        """
        if self._max_messages is None:
            return
        if len(self._messages) <= self._max_messages:
            return

        # Preserve all leading system messages, then keep only the most recent
        # user/assistant/tool messages within the remaining budget.
        system_prefix: list[dict[str, Any]] = []
        idx = 0
        for msg in self._messages:
            if msg.get("role") == "system":
                system_prefix.append(msg)
                idx += 1
            else:
                break

        tail = self._messages[idx:]
        if not tail:
            self._messages = system_prefix[-self._max_messages :]  # Worst case, only system messages
            return

        remaining = max(self._max_messages - len(system_prefix), 0)
        if remaining <= 0:
            # Budget exhausted by system messages; keep the most recent ones.
            self._messages = (system_prefix + tail)[-self._max_messages :]
            return

        self._messages = system_prefix + tail[-remaining:]

    def modify_message(
        self,
        index: int,
        modifier: Any,
    ) -> bool:
        """
        Modify a message at a specific index atomically.

        Args:
            index: The index of the message to modify.
            modifier: Either a dict to update with, or a callable that
                     takes the message and returns the modified message.

        Returns:
            True if modification succeeded, False if index out of range.
        """
        with self._lock:
            if index < 0 or index >= len(self._messages):
                return False
            if callable(modifier):
                self._messages[index] = modifier(self._messages[index])
            else:
                self._messages[index].update(modifier)
            self._version += 1
            return True

    def __len__(self) -> int:
        """Return the number of messages in the store."""
        with self._lock:
            return len(self._messages)

    @property
    def version(self) -> int:
        """
        Current version number for change detection.

        Incremented on every modification. Can be used for optimistic
        concurrency checks or cache invalidation.
        """
        with self._lock:
            return self._version

    def iter_messages(self) -> list[dict[str, Any]]:
        """
        Return a snapshot for iteration.

        This is equivalent to snapshot() but named explicitly for
        iteration use cases.

        Returns:
            A shallow copy suitable for iteration.
        """
        return self.snapshot()
