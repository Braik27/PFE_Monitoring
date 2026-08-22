"""
core/scheduler.py — DEPRECATED: remplacé par core/sla_monitor.py (SLA dynamique).

Conservé pour référence / rollback. Ne plus appeler start_scheduler() depuis app.py.
"""

import os
import logging
from datetime import datetime, timedelta
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from apscheduler.schedulers.background import BackgroundScheduler
from storage import get_storage

log = logging.getLogger("scheduler")

# Background scheduler instance
scheduler = BackgroundScheduler(timezone="UTC")

# Statuts surveillés pour dépassement SLA (explicite — ne pas élargir sans revue)
SLA_MONITORED_STATUSES = frozenset({
    "NEW", "PENDING", "ACKNOWLEDGED", "IN_PROGRESS", "ESCALATED",
})

# Statuts exclus du scan SLA — jamais de breach email ni flag pour ceux-ci
SLA_EXCLUDED_STATUSES = frozenset({
    "IGNORED", "RESOLVED", "CLOSED",
})


def parse_date(date_str: str) -> datetime:
    """Robustly parses ISO or custom dates from sqlite/Azure SQL strings."""
    if not date_str:
        return datetime.utcnow()
    # Replace 'T' and strip timezone offsets
    clean_str = date_str.split(".")[0].replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(clean_str[:19], fmt)
        except ValueError:
            pass
    return datetime.utcnow()


def send_sla_email(alert: dict):
    """Sends an SLA Breach warning email to the consultant / administrator."""
    smtp_host = os.environ.get("ALERT_SMTP_HOST", "")
    smtp_port = os.environ.get("ALERT_SMTP_PORT", "587")
    smtp_user = os.environ.get("ALERT_SMTP_USER", "")
    smtp_pass = os.environ.get("ALERT_SMTP_PASSWORD", "")
    to_addr = os.environ.get("ALERT_EMAIL_TO", "")
    from_addr = os.environ.get("ALERT_EMAIL_FROM", smtp_user or "noreply@fluxmonitor.timsoft.com")

    if not smtp_host or not smtp_user or not to_addr:
        log.warning("[SLA-MAIL] SMTP or recipient not configured, skipping SLA email alert.")
        return

    base_url = os.environ.get("APP_BASE_URL", "https://flask-trainer-app-f8bpdvavegh2gjh2.francecentral-01.azurewebsites.net")
    dashboard_url = f"{base_url}/"
    subject = f"🚨 [Flux Monitor] SLA DÉPASSÉ - Action immédiate requise - {alert.get('flux_name')}"

    body_html = f"""<html><body style='font-family:Arial,sans-serif;background:#fff5f5;padding:20px'>
<div style='max-width:600px;margin:0 auto;background:#fff;border-radius:10px;padding:30px;border:1px solid #feb2b2'>
  <h2 style='color:#e53e3e;margin-bottom:8px'>🚨 Alerte SLA Dépassé (4h)</h2>
  <p style='color:#4a5568;font-size:14px'>L'alerte suivante a dépassé la limite de résolution de 4 heures et nécessite une intervention immédiate.</p>
  
  <div style='background:#fff5f5;border:1px solid #fed7d7;border-radius:8px;padding:16px;margin:16px 0'>
    <p style='margin:4px 0'><b>📋 Flux :</b> {alert.get('flux_name')} ({alert.get('flux_id')})</p>
    <p style='margin:4px 0'><b>🔍 Description :</b> {alert.get('label')}</p>
    <p style='margin:4px 0'><b>⚠️ Anomalies critiques :</b> {alert.get('n_critiques', 0)}</p>
    <p style='margin:4px 0'><b>⚡ Concordance :</b> {alert.get('concordance', 0)}%</p>
    <p style='margin:4px 0'><b>📅 Créée le :</b> {alert.get('created_at')}</p>
    <p style='margin:4px 0'><b>🔑 Token :</b> <code style='background:#edf2f7;padding:2px 6px;border-radius:4px'>{alert.get('token')[:12]}…</code></p>
  </div>

  <div style='margin-bottom:20px'>
    <a href='{base_url}/?alert_token={alert.get("token")}' style='background:#e53e3e;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:700;display:inline-block;font-size:14px'>🔍 Ouvrir l'alerte sur le Dashboard</a>
  </div>

  <div style='padding:14px;background:#edf2f7;border-radius:8px;font-size:12px;color:#718096'>
    <strong>Flux Monitor — TimSoft</strong><br>
    Cet email automatique a été généré car l'alerte a dépassé le délai réglementaire SLA de 4h.
  </div>
</div></body></html>"""

    msg = MIMEMultipart("alternative")
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, int(smtp_port), timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, [to_addr], msg.as_string())
        log.info("[SLA-MAIL] Notification email envoyée avec succès à %s", to_addr)
    except Exception as e:
        log.error("[SLA-MAIL] Échec de l'envoi de l'email SLA : %s", e)


def check_sla_breaches():
    """Checks all unresolved alerts and marks those that exceeded the 4-hour SLA."""
    log.info("[SLA-JOB] Scan des alertes pour dépassement de SLA...")
    try:
        db = get_storage()
        alerts = db.list_alerts(limit=500, status_not_in=["CLOSED", "RESOLVED"])
        now = datetime.utcnow()
        count = 0

        for alert in alerts:
            status = alert.get("status", "")
            # Exclusion explicite : IGNORED / RESOLVED / CLOSED ne sont jamais scannés
            if status in SLA_EXCLUDED_STATUSES:
                continue
            # Surveillance active : inclut ESCALATED (alerte escaladée reste sous SLA)
            if status in SLA_MONITORED_STATUSES:
                if not alert.get("sla_breached", 0):
                    created_at = parse_date(alert.get("created_at"))
                    # If older than 4 hours
                    if (now - created_at) > timedelta(hours=4):
                        log.warning("[SLA-JOB] Alerte %s a dépassé le SLA (créée le %s)", alert.get("token")[:12], created_at)
                        
                        # Flag in DB
                        db.flag_sla_breached(alert.get("token"))
                        
                        # Save tracking
                        db.save_tracking(
                            alert_token=alert.get("token"),
                            username="system",
                            action="SLA_BREACHED",
                            comment="Délai réglementaire de 4h dépassé. Alerte signalée."
                        )
                        
                        # Send email alert
                        send_sla_email(alert)
                        count += 1
                        
        if count > 0:
            log.info("[SLA-JOB] Scan terminé. %d alertes signalées.", count)
    except Exception as e:
        log.error("[SLA-JOB] Erreur lors du scan SLA : %s", e)


def start_scheduler():
    """Starts the background scheduler task."""
    if not scheduler.running:
        # Check every 5 minutes
        scheduler.add_job(check_sla_breaches, "interval", minutes=5, id="sla_breach_check")
        scheduler.start()
        log.info("APScheduler démarré avec succès. Tâche check_sla_breaches planifiée toutes les 5 minutes.")
