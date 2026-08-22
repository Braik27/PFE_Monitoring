"""
scheduler_worker.py — Standalone APScheduler for local development.

Runs SLA monitoring and daily reports independently of the Flask app.
Activated via ENABLE_SCHEDULER=true.

Usage:
    ENABLE_SCHEDULER=true python scheduler_worker.py
"""
from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("scheduler_worker")


def main():
    from dotenv import load_dotenv
    load_dotenv()

    from storage import get_storage
    from core.sla_monitor import monitor_sla_job
    from api.daily_report import send_daily_report

    storage = get_storage()
    storage.init_db()

    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler(timezone="UTC")

        scheduler.add_job(
            monitor_sla_job,
            args=[storage, None],
            trigger="interval",
            minutes=5,
            id="sla_monitor",
            replace_existing=True,
        )

        scheduler.add_job(
            send_daily_report,
            trigger="cron",
            hour=8,
            minute=0,
            id="daily_report",
            replace_existing=True,
        )

        scheduler.start()
        log.info("[Scheduler] Demare (SLA 5min + daily 08:00 UTC)")

        if os.environ.get("QUEUE_BACKEND", "local") == "local":
            from core.local_worker import start_local_worker
            start_local_worker()
            log.info("[Scheduler] Local queue worker demarre")

        import time
        while True:
            time.sleep(60)

    except KeyboardInterrupt:
        log.info("[Scheduler] Arret demande")
        scheduler.shutdown()
    except Exception as exc:
        log.error("[Scheduler] Erreur: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
