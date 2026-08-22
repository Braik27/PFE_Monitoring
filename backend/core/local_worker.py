"""
core/local_worker.py — Local queue polling worker.

Consumes messages from the queue backend (Azurite in dev),
runs engine/pipeline.py, and writes results via storage/base.py.

Activated when QUEUE_BACKEND=local.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

MAX_RETRIES = 3
POLL_INTERVAL = int(os.environ.get("SLA_POLL_INTERVAL_SECONDS", "5"))


def _process_message(msg: dict, storage=None) -> bool:
    """Process a single queue message. Returns True on success."""
    from engine.pipeline import run_analysis, AnalysisRequest

    body = msg.get("body", {})
    job_id = body.get("job_id", "")
    flux_id = body.get("flux_id", "")
    blob_cegid = body.get("blob_path_cegid", "")
    blob_oracle = body.get("blob_path_oracle", "")
    division = body.get("division", "")
    analyst = body.get("analyst", "")

    if not flux_id or not blob_cegid or not blob_oracle:
        log.warning("[Worker] Message incomplet: %s", body)
        return True  # Drop incomplete messages

    if storage is None:
        from storage import get_storage
        storage = get_storage()

    storage.update_job(
        job_id, status="RUNNING", step_label="Traitement local en cours...",
    )

    request = AnalysisRequest(
        flux_id=flux_id,
        label=f"Analyse locale {flux_id} ({analyst or 'auto'})",
        pairs=[
            {"cegid": blob_cegid, "oracle": blob_oracle}
        ],
        forced_division=division,
    )

    def update_progress(step_key, extra_label=""):
        from engine.pipeline import STEPS
        step = STEPS.get(step_key, {})
        storage.update_job(
            job_id,
            progress=step.get("pct", 0),
            step_label=extra_label or step.get("label", step_key),
        )

    try:
        result = run_analysis(request)
        from storage.base import json_encode
        storage.update_job(
            job_id,
            status="SUCCESS",
            progress=100,
            step_label="Analyse terminee",
            result_json=json_encode({
                "flux_id": result.flux_id,
                "flux_name": result.flux_name,
                "label": result.label,
                "total_critiques": result.total_critiques,
                "total_warnings": result.total_warnings,
                "concordance": result.concordance_moyenne,
                "divisions": result.divisions_found,
            }),
            ended_at=time.time(),
        )

        from core.email_alert import send_alert_async
        send_alert_async(result, analysis_id=0)

        log.info("[Worker] Job %s termine: %d crit, %d warn",
                 job_id[:12], result.total_critiques, result.total_warnings)
        return True

    except Exception as exc:
        log.exception("[Worker] Job %s echoue: %s", job_id[:12], exc)
        storage.update_job(
            job_id,
            status="FAILED",
            error=str(exc),
            ended_at=time.time(),
        )
        return False


def start_local_worker(interval: int = POLL_INTERVAL) -> threading.Thread:
    """Start the local queue polling worker in a daemon thread."""
    def _loop():
        from core.queue_backends import get_queue_backend
        from storage import get_storage
        queue = get_queue_backend()
        storage = get_storage()
        log.info("[Worker] Demarrage du polling (intervalle %ds)", interval)
        while True:
            try:
                msg = queue.dequeue()
                if msg is None:
                    time.sleep(interval)
                    continue
                retries = msg.get("body", {}).get("_retries", 0)
                success = _process_message(msg, storage=storage)
                if success:
                    try:
                        queue.delete(msg["id"])
                    except Exception:
                        pass
                elif retries < MAX_RETRIES:
                    msg["body"]["_retries"] = retries + 1
                    try:
                        queue.enqueue(msg["body"])
                        queue.delete(msg["id"])
                    except Exception:
                        pass
                else:
                    log.error("[Worker] Max retries atteint pour %s", msg.get("body", {}).get("job_id", "?"))
                    try:
                        queue.delete(msg["id"])
                    except Exception:
                        pass
            except Exception as exc:
                log.error("[Worker] Erreur boucle: %s", exc)
                time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="local-queue-worker")
    t.start()
    return t
