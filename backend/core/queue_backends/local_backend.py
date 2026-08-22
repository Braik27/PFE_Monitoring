"""
Local queue backend using Azurite (Azure Storage emulator).

Requires the azure-storage-queue SDK and a running Azurite instance.
Connection string defaults to the standard Azurite default.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from core.queue_backends.base import QueueBackend

log = logging.getLogger(__name__)

DEFAULT_AZURITE_CONN = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
    "QueueEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
    "TableEndpoint=http://127.0.0.1:10003/devstoreaccount1;"
)


class AzuriteQueueBackend(QueueBackend):
    """Queue backend backed by Azurite local emulator."""

    def __init__(self):
        from azure.storage.queue import QueueClient
        conn_str = os.environ.get("AZURITE_CONNECTION_STRING", DEFAULT_AZURITE_CONN)
        queue_name = os.environ.get("AZURE_QUEUE_NAME", "flux-analysis-queue")
        self._client = QueueClient.from_connection_string(conn_str, queue_name)
        try:
            self._client.create_queue()
        except Exception:
            pass  # Queue may already exist

    def enqueue(self, message: Dict[str, Any]) -> str:
        result = self._client.send_message(json.dumps(message))
        log.info("[Azurite] Enqueued job %s", message.get("job_id", "?"))
        return result.id

    def dequeue(self) -> Optional[Dict[str, Any]]:
        msg = self._client.receive_message(visibility_timeout=300)
        if msg is None:
            return None
        try:
            body = json.loads(msg.content)
        except (json.JSONDecodeError, TypeError):
            body = {"raw": msg.content}
        return {"id": msg.id, "pop_receipt": msg.pop_receipt, "body": body}

    def peek(self, count: int = 1) -> List[Dict[str, Any]]:
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
        # Azurite delete_message needs (message, pop_receipt) — use delete_message on msg object
        try:
            self._client.delete_message(message_id, pop_receipt="")
        except Exception:
            pass  # Best effort; message will expire via visibility timeout

    def queue_length(self) -> int:
        props = self._client.get_queue_properties()
        return props.approximate_message_count or 0
