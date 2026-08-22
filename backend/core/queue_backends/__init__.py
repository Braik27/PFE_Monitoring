"""
Queue Backend abstraction — factory for local (Azurite) or Azure queue.

Selection via QUEUE_BACKEND=local|azure (default "local").
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

_backend_instance = None


def get_queue_backend():
    """Return the singleton queue backend based on QUEUE_BACKEND env var."""
    global _backend_instance
    if _backend_instance is not None:
        return _backend_instance

    backend_type = os.environ.get("QUEUE_BACKEND", "local").lower()

    if backend_type == "azure":
        from core.queue_backends.azure_backend import AzureQueueBackend
        _backend_instance = AzureQueueBackend()
        log.info("[Queue] Backend Azure (wraps core/queue_client.py)")
    else:
        from core.queue_backends.local_backend import AzuriteQueueBackend
        _backend_instance = AzuriteQueueBackend()
        log.info("[Queue] Backend local Azurite")

    return _backend_instance
