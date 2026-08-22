"""
Azure queue backend — thin wrapper around core/queue_client.py.

DO NOT modify core/queue_client.py. This module only imports and calls it.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from core.queue_backends.base import QueueBackend

log = logging.getLogger(__name__)


class AzureQueueBackend(QueueBackend):
    """Queue backend that wraps the existing core/queue_client.py functions."""

    def __init__(self):
        from azure.storage.queue import QueueClient
        conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
        queue_name = os.environ.get("AZURE_QUEUE_NAME", "flux-analysis-queue")
        if not conn_str:
            log.warning("[AzureQueue] AZURE_STORAGE_CONNECTION_STRING non configure")
            self._client = None
            return
        self._client = QueueClient.from_connection_string(conn_str, queue_name)
        try:
            self._client.create_queue()
        except Exception:
            pass

    def enqueue(self, message: Dict[str, Any]) -> str:
        from core.queue_client import enqueue_comparison_job
        enqueue_comparison_job(
            job_id=message.get("job_id", ""),
            flux_id=message.get("flux_id", ""),
            blob_path_cegid=message.get("blob_path_cegid", ""),
            blob_path_oracle=message.get("blob_path_oracle", ""),
            division=message.get("division", ""),
            analyst=message.get("analyst", ""),
        )
        log.info("[AzureQueue] Enqueued via queue_client: job %s", message.get("job_id"))
        return message.get("job_id", "")

    def dequeue(self) -> Optional[Dict[str, Any]]:
        if not self._client:
            return None
        msg = self._client.receive_message(visibility_timeout=300)
        if msg is None:
            return None
        try:
            body = json.loads(msg.content)
        except (json.JSONDecodeError, TypeError):
            body = {"raw": msg.content}
        return {"id": msg.id, "pop_receipt": msg.pop_receipt, "body": body}

    def peek(self, count: int = 1) -> List[Dict[str, Any]]:
        if not self._client:
            return []
        messages = self._client.peek_messages(count)
        result = []
        for msg in (messages if hasattr(messages, '__iter__') else [messages]):
            try:
                body = json.loads(msg.content)
            except (json.JSONDecodeError, TypeError):
                body = {"raw": msg.content}
            result.append({"id": msg.id, "body": body})
        return result

    def delete(self, message_id: str) -> None:
        pass  # Deletion handled by Azure Function after processing

    def queue_length(self) -> int:
        if not self._client:
            return 0
        props = self._client.get_queue_properties()
        return props.approximate_message_count or 0
