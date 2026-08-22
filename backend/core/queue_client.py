import os
import json
from azure.storage.queue import QueueClient

_connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
_queue_name = os.getenv("AZURE_QUEUE_NAME", "flux-analysis-queue")

def enqueue_comparison_job(job_id, flux_id, blob_path_cegid, blob_path_oracle,
                            division, analyst):
    client = QueueClient.from_connection_string(_connection_string, _queue_name)
    message = {
        "job_id": job_id,
        "flux_id": flux_id,
        "blob_path_cegid": blob_path_cegid,
        "blob_path_oracle": blob_path_oracle,
        "division": division,
        "analyst": analyst,
        "status": "pending",
    }
    client.send_message(json.dumps(message))