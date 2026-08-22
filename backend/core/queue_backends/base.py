"""
Abstract base class for queue backends.

Mirrors the interface expected by both the Azure Function (production)
and the local polling worker (development).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class QueueBackend(ABC):
    """Abstract queue backend with enqueue / dequeue / peek / delete."""

    @abstractmethod
    def enqueue(self, message: Dict[str, Any]) -> str:
        """
        Push a message onto the queue.

        Args:
            message: Dict with keys matching the Azure Function contract
                     (job_id, flux_id, blob_path_cegid, blob_path_oracle,
                      division, analyst, status).

        Returns:
            Message ID (str) assigned by the backend.
        """

    @abstractmethod
    def dequeue(self) -> Optional[Dict[str, Any]]:
        """
        Pop the next message from the queue.

        Returns:
            Dict with 'id' (message ID) and 'body' (parsed JSON dict),
            or None if the queue is empty.
        """

    @abstractmethod
    def peek(self, count: int = 1) -> List[Dict[str, Any]]:
        """
        Peek at the next *count* messages without removing them.

        Returns:
            List of dicts with 'id' and 'body' keys.
        """

    @abstractmethod
    def delete(self, message_id: str) -> None:
        """
        Delete a message from the queue (after successful processing).
        """

    @abstractmethod
    def queue_length(self) -> int:
        """Return approximate number of messages in the queue."""
