import os
from storage.base import BaseStorage

_instance = None

def get_storage() -> BaseStorage:
    global _instance
    if _instance is None:
        backend = os.environ.get("STORAGE_BACKEND", "local").lower()
        if backend == "azure":
            from storage.azure_backend import AzureStorage
            _instance = AzureStorage()
        else:
            from storage.local import LocalStorage
            _instance = LocalStorage()
        _instance.init_db()
    return _instance