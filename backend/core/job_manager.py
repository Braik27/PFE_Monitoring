"""
core/job_manager.py — Gestionnaire de tâches asynchrones pour Flux Monitor

⚠️ NOTE ARCHITECTURE — il existe DEUX systèmes de jobs distincts, volontairement :
  1. CE MODULE (JobManager) : comparaisons interactives « smart compare ».
     API : /api/smart/run-async, /api/smart/jobs/<id>[​/result] (smart_compare_async.py).
     Progression fine (STEPS + WebSocket), ThreadPoolExecutor(4), récupération au
     démarrage (recover_jobs), TTL + expiration des résultats.
     Statuts écrits en base : PENDING/RUNNING/SUCCESS/FAILED/EXPIRED
     (re-mappés DONE/ERROR dans les réponses API).
  2. Les helpers raw-SQL create/update/get_job_async (storage) utilisés par
     POST /api/flux/comparer + GET /api/flux/jobs/<id> (flux_api.py) : analyses
     de flux fire-and-forget, polling simple par le frontend, thread unique ou
     file Azure. Statuts : PENDING/RUNNING/DONE/ERROR.

Les deux partagent la MÊME table `jobs` avec des vocabulaires différents :
ne pas fusionner sans migration (consommateurs frontend distincts,
useAsyncJob.ts vs AsyncAnalysisProgress.tsx ; cleanup_jobs/recover_jobs
raisonnent sur les statuts du système 1 uniquement — cf. REVIEW_DEEP.md).
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Callable, Optional
from storage import get_storage
from storage.base import json_encode

log = logging.getLogger("job_manager")

JOB_TTL_SECONDS = 86400  # Persist jobs for 24h
MAX_WORKERS     = 4


class JobStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE    = "DONE"
    ERROR   = "ERROR"
    SUCCESS = "SUCCESS"
    FAILED  = "FAILED"
    EXPIRED = "EXPIRED"


STEPS = {
    "created":   {"pct": 0,   "label": "Job créé, en attente..."},
    "reading":   {"pct": 10,  "label": "Lecture des fichiers CSV..."},
    "cleaning":  {"pct": 30,  "label": "Nettoyage et normalisation des données..."},
    "comparing": {"pct": 50,  "label": "Comparaison Cegid ↔ Oracle en cours..."},
    "ia":        {"pct": 70,  "label": "Enrichissement IA des anomalies..."},
    "saving":    {"pct": 90,  "label": "Sauvegarde des résultats..."},
    "done":      {"pct": 100, "label": "Analyse terminée ✅"},
}


class Job:
    def __init__(self, job_id: str, job_type: str, meta: dict = None):
        self.job_id     = job_id
        self.job_type   = job_type
        self.status     = JobStatus.PENDING
        self.progress   = 0
        self.step_label = "En attente..."
        self.result     = None
        self.error      = None
        self.meta       = meta or {}
        self.created_at = datetime.now()
        self.started_at: Optional[datetime] = None
        self.ended_at:   Optional[datetime] = None

    @classmethod
    def from_dict(cls, d: dict) -> Job:
        def parse_dt(s):
            if not s:
                return None
            if isinstance(s, datetime):
                return s
            try:
                return datetime.fromisoformat(s)
            except Exception:
                return None

        job = cls(job_id=d["id"], job_type=d["job_type"], meta=d.get("meta", {}))
        # Map database status back to public interface status (DONE/ERROR) for backward compatibility
        db_status = d["status"]
        if db_status == "SUCCESS":
            job.status = JobStatus.DONE
        elif db_status == "FAILED":
            job.status = JobStatus.ERROR
        else:
            job.status = db_status

        job.progress = d["progress"]
        job.step_label = d["step_label"]
        
        if d.get("result_json"):
            try:
                job.result = json.loads(d["result_json"])
            except Exception:
                job.result = None
        
        job.error = d["error"]
        job.created_at = parse_dt(d["created_at"]) or datetime.now()
        job.started_at = parse_dt(d["started_at"])
        job.ended_at = parse_dt(d["ended_at"])
        return job

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at and self.ended_at:
            return round((self.ended_at - self.started_at).total_seconds(), 1)
        return None

    def to_dict(self) -> dict:
        return {
            "job_id":           self.job_id,
            "job_type":         self.job_type,
            "status":           self.status,
            "progress":         self.progress,
            "step_label":       self.step_label,
            "error":            self.error,
            "meta":             self.meta,
            "created_at":       self.created_at.isoformat(),
            "started_at":       self.started_at.isoformat() if self.started_at else None,
            "ended_at":         self.ended_at.isoformat()   if self.ended_at   else None,
            "duration_seconds": self.duration_seconds,
        }


class JobManager:
    def __init__(self):
        self._lock     = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self._broadcast: Optional[Callable] = None
        self._start_cleanup_thread()

    def set_broadcaster(self, fn: Callable):
        self._broadcast = fn

    def recover_jobs(self):
        """Marks incomplete jobs as failed upon server startup/restart."""
        try:
            storage = get_storage()
            incomplete = storage.get_incomplete_jobs()
            if incomplete:
                log.info("[JM] Restauration : %d jobs incomplets trouvés. Marquage en FAILED.", len(incomplete))
                for j in incomplete:
                    storage.update_job(
                        j["id"],
                        status="FAILED",
                        error="Server restarted",
                        ended_at=datetime.now()
                    )
        except Exception as e:
            log.exception("[JM] Échec de la restauration des jobs : %s", e)

    def submit(self, job_type, fn, fn_args=(), fn_kwargs=None, meta=None) -> str:
        job_id = uuid.uuid4().hex
        storage = get_storage()
        
        # Save initially as PENDING in DB
        storage.save_job(
            job_id=job_id,
            job_type=job_type,
            status=JobStatus.PENDING,
            progress=0,
            step_label="En attente...",
            meta=meta or {}
        )

        job = Job(job_id=job_id, job_type=job_type, meta=meta or {})
        self._broadcast_progress(job)
        log.info("[JM] Job soumis en DB : %s (%s)", job_id, job_type)

        def _run():
            storage.update_job(
                job_id,
                status=JobStatus.RUNNING,
                started_at=datetime.now()
            )
            
            j_dict = storage.get_job(job_id)
            if j_dict:
                self._broadcast_progress(Job.from_dict(j_dict))

            def update_progress(step_key: str, extra_label: str = ""):
                step = STEPS.get(step_key, {})
                pct = step.get("pct", 0)
                lbl = extra_label or step.get("label", step_key)
                
                storage.update_job(
                    job_id,
                    progress=pct,
                    step_label=lbl
                )
                
                curr_dict = storage.get_job(job_id)
                if curr_dict:
                    curr_job = Job.from_dict(curr_dict)
                    self._broadcast_progress(curr_job)
                    log.debug("[JM] %s → %s%% — %s", job_id, curr_job.progress, curr_job.step_label)

            try:
                result = fn(update_progress, *(fn_args or ()), **(fn_kwargs or {}))
                
                storage.update_job(
                    job_id,
                    status="SUCCESS",
                    progress=100,
                    step_label=STEPS["done"]["label"],
                    result_json=json_encode(result),
                    ended_at=datetime.now()
                )
                
                final_dict = storage.get_job(job_id)
                if final_dict:
                    final_job = Job.from_dict(final_dict)
                    log.info("[JM] Job terminé : %s en %ss", job_id, final_job.duration_seconds)
            except Exception as exc:
                storage.update_job(
                    job_id,
                    status="FAILED",
                    error=str(exc),
                    ended_at=datetime.now()
                )
                log.exception("[JM] Job échoué : %s — %s", job_id, exc)
            finally:
                # Broadcast status updates to late clients
                def broadcast_status():
                    try:
                        latest_dict = storage.get_job(job_id)
                        if latest_dict:
                            self._broadcast_progress(Job.from_dict(latest_dict))
                    except Exception:
                        pass

                broadcast_status()
                threading.Timer(0.5, broadcast_status).start()
                threading.Timer(2.0, broadcast_status).start()
                threading.Timer(5.0, broadcast_status).start()

        self._executor.submit(_run)
        return job_id

    def get(self, job_id: str) -> Optional[Job]:
        try:
            j_dict = get_storage().get_job(job_id)
            return Job.from_dict(j_dict) if j_dict else None
        except Exception:
            return None

    def get_result_and_cleanup(self, job_id: str) -> Optional[dict]:
        job = self.get(job_id)
        if not job or job.status != JobStatus.DONE:
            return None
        
        # Load the result from the DB
        try:
            storage = get_storage()
            j_dict = storage.get_job(job_id)
            if not j_dict or not j_dict.get("result_json"):
                return None
            result = json.loads(j_dict["result_json"])
            
            # Expire the job in the DB instead of fully deleting immediately
            storage.update_job(job_id, status="EXPIRED")
            return result
        except Exception:
            return None

    def _broadcast_progress(self, job: Job):
        if self._broadcast:
            try:
                self._broadcast(job)
            except Exception as e:
                log.debug("[JM] Broadcast erreur: %s", e)

    def _cleanup_old_jobs(self):
        try:
            get_storage().cleanup_jobs(JOB_TTL_SECONDS)
        except Exception as e:
            log.warning("[JM] Erreur nettoyage jobs: %s", e)

    def _start_cleanup_thread(self):
        def _loop():
            while True:
                time.sleep(600)
                self._cleanup_old_jobs()
        threading.Thread(target=_loop, daemon=True, name="job-cleanup").start()


_manager: Optional[JobManager] = None
_manager_lock = threading.Lock()


def get_job_manager() -> JobManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = JobManager()
                _manager.recover_jobs()
    return _manager