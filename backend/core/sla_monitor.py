"""
SLA Monitor — surveillance dynamique, rappels, breach email/report (idempotent).

Auto-escalade et auto-close supprimés : 100% manuel conformément au PLAN.

SLA Warning: envoyé à 75% du SLA écoulé (25% restant), UNE SEULE FOIS par alerte.
SLA Breach:  envoyé quand le SLA est complètement dépassé, UNE SEULE FOIS par alerte.
Si l'alerte est résolue → plus aucun email SLA.
"""

from __future__ import annotations

import logging
import os
import smtplib
import threading
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from core.sla_policy import (
    SLA_EXCLUDED_STATUSES,
    SLA_MONITORED_STATUSES,
    recompute_sla_progress,
    get_concordance_state,
)

log = logging.getLogger(__name__)

SYSTEM_ACTOR = {"username": "system", "role": "admin", "sub": "scheduler"}

# ─── Warning threshold: 75% elapsed = 25% remaining ────────────────────
WARNING_REMAINING_PCT = 25.0


def _smtp_send(to_addr: str, subject: str, body_html: str) -> None:
    smtp_host = os.environ.get("ALERT_SMTP_HOST", "")
    smtp_port = int(os.environ.get("ALERT_SMTP_PORT", "587"))
    smtp_user = os.environ.get("ALERT_SMTP_USER", "")
    smtp_pass = os.environ.get("ALERT_SMTP_PASSWORD", "")
    from_addr = os.environ.get("ALERT_EMAIL_FROM", smtp_user or "noreply@fluxmonitor.timsoft.com")

    if not smtp_host or not smtp_user or not to_addr:
        log.warning("[SLA] SMTP ou destinataire non configuré — email ignoré")
        return

    msg = MIMEMultipart("alternative")
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, [to_addr], msg.as_string())
        log.info("[SLA] Email envoyé → %s", to_addr)
    except Exception as exc:
        log.error("[SLA] Échec envoi email → %s : %s", to_addr, exc)


def _send_async(to_addr: str, subject: str, body_html: str) -> None:
    threading.Thread(target=_smtp_send, args=(to_addr, subject, body_html), daemon=True).start()


def _base_url() -> str:
    return os.environ.get(
        "APP_BASE_URL",
        "https://flask-trainer-app-f8bpdvavegh2gjh2.francecentral-01.azurewebsites.net",
    )


def get_current_responsible_email(storage, alert: dict) -> str:
    """Resolve the current responsible person's email for SLA warnings and breaches."""
    # 1. Priorité à l'email de la personne à qui l'alerte est escaladée
    if alert.get("escalated_to"):
        return alert["escalated_to"]

    # 2. Analyste qui a pris en charge l'alerte
    token = alert.get("token", "")
    for entry in reversed(storage.get_tracking(token) or []):
        action = (entry.get("action") or "").upper()
        if action in ("ACKNOWLEDGED", "IN_PROGRESS"):
            username = entry.get("username", "")
            for u in storage.list_users() or []:
                if u.get("username") == username and u.get("email"):
                    return u["email"]

    # 3. Consultant associé au flux
    flux_id = alert.get("flux_id", "")
    try:
        from engine.flux_loader import FluxLoader
        cfg = FluxLoader.load(flux_id)
        if cfg.consultant_email:
            return cfg.consultant_email
    except Exception:
        pass

    # 4. Fallback vers ALERT_EMAIL_TO
    return os.environ.get("ALERT_EMAIL_TO", "")


def _find_team_leader_emails(storage) -> list[str]:
    emails = []
    for u in storage.list_users() or []:
        if u.get("role") == "team_leader" and u.get("email"):
            emails.append(u["email"])
    if not emails:
        fallback = os.environ.get("ALERT_EMAIL_TO", "")
        if fallback:
            emails.append(fallback)
    return emails


def _tracking_has_action(storage, token: str, action_prefix: str) -> bool:
    for entry in storage.get_tracking(token) or []:
        if (entry.get("action") or "").startswith(action_prefix):
            return True
    return False


def _email_sla_warning(alert: dict, remaining_pct: float, to_addr: str) -> None:
    """Email SLA proche de l'expiration — envoyé à 75% du SLA écoulé."""
    base = _base_url()
    token = alert.get("token", "")
    sla_h = alert.get("sla_hours") or 4
    concordance = alert.get("concordance", "—")
    conc_state = get_concordance_state(float(concordance) if concordance != "—" else 100.0)
    elapsed = round(sla_h * (100 - remaining_pct) / 100, 1)
    remaining_h = round(sla_h * remaining_pct / 100, 1)
    severity = alert.get("severity") or alert.get("severity_class", "")
    current_status = alert.get("workflow_status") or alert.get("status", "NEW")
    responsible = alert.get("escalated_to") or alert.get("resolved_by") or "Non assigné"

    body = f"""<html><body style='font-family:Arial,sans-serif;background:#fffbeb;padding:20px'>
<div style='max-width:600px;margin:0 auto;background:#fff;border-radius:10px;padding:30px;border:1px solid #fcd34d'>
  <h2 style='color:#d97706'>⚠️ Alerte SLA proche de l'expiration</h2>
  <p style='color:#64748b;font-size:13px'>Bonjour,</p>
  <p>Le SLA de cette alerte est sur le point d'expirer. <strong>Intervention requise.</strong></p>
  <div style='background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;padding:16px;margin:16px 0'>
    <p style='margin:4px 0'><b>ID Alerte :</b> <code style='background:#f1f5f9;padding:2px 6px;border-radius:4px;font-size:11px'>{token[:16]}…</code></p>
    <p style='margin:4px 0'><b>Flux :</b> {alert.get('flux_name')} ({alert.get('flux_id')})</p>
    <p style='margin:4px 0'><b>Créée le :</b> {alert.get('created_at', '—')}</p>
    <p style='margin:4px 0'><b>SLA :</b> {sla_h}h — deadline {alert.get('sla_deadline', '—')}</p>
    <p style='margin:4px 0'><b>Temps écoulé :</b> {elapsed}h</p>
    <p style='margin:4px 0'><b>Temps restant :</b> <strong style='color:#d97706'>{remaining_h}h ({remaining_pct:.0f}%)</strong></p>
    <p style='margin:4px 0'><b>Criticité :</b> {severity or conc_state}</p>
    <p style='margin:4px 0'><b>Concordance :</b> {concordance}% ({conc_state})</p>
    <p style='margin:4px 0'><b>Statut :</b> {current_status}</p>
    <p style='margin:4px 0'><b>Responsable :</b> {responsible}</p>
  </div>
  <a href='{base}/?alert_token={token}' style='display:inline-block;background:#d97706;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:700'>Ouvrir l'alerte</a>
  <div style='margin-top:20px;padding:14px;background:#f1f5f9;border-radius:8px;font-size:12px;color:#64748b'>
    <strong>Flux Monitor — TimSoft</strong>
  </div>
</div></body></html>"""
    subject = f"⚠️ SLA proche expiration ({remaining_pct:.0f}% restant) — {alert.get('flux_name')}"
    _send_async(to_addr, subject, body)


def _email_sla_breach(alert: dict, to_addrs: list[str]) -> None:
    """Email when SLA is breached. Called exactly once per alert (idempotent via breach_email_sent)."""
    base = _base_url()
    token = alert.get("token", "")
    sla_h = alert.get("sla_hours") or 4
    severity = alert.get("severity") or alert.get("severity_class", "")
    concordance = alert.get("concordance", "—")
    conc_state = get_concordance_state(float(concordance) if concordance != "—" else 100.0)
    current_status = alert.get("workflow_status") or alert.get("status", "NEW")
    responsible = alert.get("escalated_to") or "Non assigné"
    created = alert.get("created_at", "—")
    deadline = alert.get("sla_deadline", "—")

    body = f"""<html><body style='font-family:Arial,sans-serif;background:#fff5f5;padding:20px'>
<div style='max-width:600px;margin:0 auto;background:#fff;border-radius:10px;padding:30px;border:1px solid #feb2b2'>
  <h2 style='color:#e53e3e'>🚨 SLA DÉPASSÉE — {severity or conc_state}</h2>
  <p style='color:#64748b;font-size:13px'>Bonjour,</p>
  <p>Le délai SLA de cette alerte est <strong>totalement dépassé</strong>. Une action immédiate est requise.</p>
  <div style='background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:16px;margin:16px 0'>
    <p style='margin:4px 0'><b>ID Alerte :</b> <code style='background:#f1f5f9;padding:2px 6px;border-radius:4px;font-size:11px'>{token[:16]}…</code></p>
    <p style='margin:4px 0'><b>Flux :</b> {alert.get('flux_name')} ({alert.get('flux_id')})</p>
    <p style='margin:4px 0'><b>Créée le :</b> {created}</p>
    <p style='margin:4px 0'><b>SLA configurée :</b> {sla_h}h</p>
    <p style='margin:4px 0'><b>Heure d expiration :</b> {deadline}</p>
    <p style='margin:4px 0'><b>Concordance :</b> {concordance}% ({conc_state})</p>
    <p style='margin:4px 0'><b>Criticité :</b> {severity or conc_state}</p>
    <p style='margin:4px 0'><b>Statut actuel :</b> {current_status}</p>
    <p style='margin:4px 0'><b>Responsable :</b> {responsible}</p>
  </div>
  <a href='{base}/?alert_token={token}' style='display:inline-block;background:#e53e3e;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:700'>Intervenir maintenant</a>
  <div style='margin-top:20px;padding:14px;background:#f1f5f9;border-radius:8px;font-size:12px;color:#64748b'>
    <strong>Flux Monitor — TimSoft</strong>
  </div>
</div></body></html>"""
    subject = f"🚨 SLA DÉPASSÉE — {alert.get('flux_name')} ({conc_state})"
    for addr in to_addrs:
        _send_async(addr, subject, body)


def _send_breach_report_email(alert: dict, to_addrs: list[str]) -> None:
    """Send single-alert report on breach. Idempotent via breach_report_sent flag."""
    from engine.detailed_report import build_single_alert_report
    report = build_single_alert_report(alert)
    base = _base_url()
    token = report["token"]
    body = f"""<html><body style='font-family:Arial,sans-serif;background:#fff;padding:20px'>
<div style='max-width:600px;margin:0 auto;background:#fff;border-radius:10px;padding:30px;border:1px solid #dde3f0'>
  <h2 style='color:#1d4ed8'>📊 Rapport d'alerte</h2>
  <p><b>Flux :</b> {report['flux_name']} ({report['flux_id']})</p>
  <p><b>Concordance :</b> {report['concordance']}%</p>
  <p><b>Sévérité :</b> {report['severity'] or report.get('severity_class', '—')}</p>
  <p><b>SLA Status :</b> {report['sla_status']}</p>
  <p><b>Créée le :</b> {report['created_at']}</p>
  <p><a href='{base}/?alert_token={token}' style='background:#1d4ed8;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:700'>Voir les détails</a></p>
</div></body></html>"""
    subject = f"📊 Rapport alerte — {report['flux_name']}"
    for addr in to_addrs:
        _send_async(addr, subject, body)


def _get_consultant_email(storage, alert: dict) -> str:
    """Resolve consultant email: registry → DEFAULT_CONSULTANT_EMAIL → log warning."""
    flux_id = alert.get("flux_id", "")
    try:
        from engine.flux_loader import FluxLoader
        cfg = FluxLoader.load(flux_id)
        if cfg.consultant_email:
            return cfg.consultant_email
    except Exception:
        pass
    default = os.environ.get("DEFAULT_CONSULTANT_EMAIL", "")
    if default:
        return default
    log.warning("[SLA] Aucun email consultant configuré pour flux %s — email ignoré", flux_id)
    return ""


def monitor_sla_job(storage, event_bus=None):
    """Job périodique : recalcule SLA, rappels à 75% écoulé, breach email/report (idempotent)."""
    from core.alert_state_machine import transition_alert, InvalidTransitionError

    try:
        open_alerts = storage.list_alerts(limit=500, status_not_in=["CLOSED", "IGNORED", "RESOLVED"])
        breached_count = 0
        warning_count = 0

        for alert in open_alerts:
            wf_status = alert.get("workflow_status") or alert.get("status", "")
            if wf_status in SLA_EXCLUDED_STATUSES:
                continue
            if wf_status not in SLA_MONITORED_STATUSES:
                continue

            token = alert.get("token", "")
            sla_data = recompute_sla_progress(alert)
            storage.update_sla_fields(token, sla_data)

            remaining_pct = sla_data["remaining_pct"]
            breached = sla_data["breached"]
            was_breached = bool(alert.get("sla_breached"))

            # ── Warning à 75% écoulé (25% restant ou moins) — UNE SEULE FOIS ──────
            if not breached and remaining_pct <= WARNING_REMAINING_PCT:
                if not alert.get("sla_warning_sent"):
                    responsible_email = get_current_responsible_email(storage, alert)
                    if responsible_email:
                        _email_sla_warning(alert, remaining_pct, responsible_email)
                        storage.set_sla_warning_sent(token)
                        storage.save_tracking(
                            alert_token=token,
                            username="system",
                            action="SLA_WARNING",
                            comment=f"Email SLA proche — {remaining_pct:.0f}% restant",
                        )
                        warning_count += 1

            # ── Breach : flag + email idempotent + report idempotent ──────────
            if breached:
                if not was_breached:
                    storage.flag_sla_breached(token)
                    storage.update_sla_status(token, "BREACHED", audit_username="system")
                    storage.save_tracking(
                        alert_token=token,
                        username="system",
                        action="SLA_BREACHED",
                        comment=f"SLA dépassé ({alert.get('sla_hours', 4)}h)",
                    )
                    breached_count += 1

                # Email breach (idempotent)
                if not alert.get("breach_email_sent"):
                    responsible_email = get_current_responsible_email(storage, alert)
                    if responsible_email:
                        _email_sla_breach(alert, [responsible_email])
                    storage.set_breach_email_sent(token)

                # Report breach (idempotent)
                if not alert.get("breach_report_sent"):
                    team_leaders = _find_team_leader_emails(storage)
                    if team_leaders:
                        _send_breach_report_email(alert, team_leaders)
                    storage.set_breach_report_sent(token)

            # ── AT_RISK if not yet breached and <=25% ────────────────────────────
            if not breached and remaining_pct <= WARNING_REMAINING_PCT:
                current_sla = alert.get("sla_status", "ON_TIME")
                if current_sla != "AT_RISK":
                    storage.update_sla_status(token, "AT_RISK", audit_username="system")

            if event_bus and breached:
                event_bus.publish("alert.sla.breach", {
                    "token": token,
                    "flux_id": alert.get("flux_id"),
                    "sla_deadline": sla_data["sla_deadline"],
                })

        log.info(
            "[SLA Monitor] %d alertes — %d breach, %d rappels",
            len(open_alerts), breached_count, warning_count,
        )
    except Exception as exc:
        log.exception("[SLA Monitor] Erreur : %s", exc)


def init_sla_scheduler(app, storage, event_bus=None):
    """Démarre le scheduler SLA (surveillance 5min)."""
    from apscheduler.schedulers.background import BackgroundScheduler

    try:
        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(
            monitor_sla_job,
            args=[storage, event_bus],
            trigger="interval",
            minutes=5,
            id="sla_monitor",
            replace_existing=True,
        )
        scheduler.start()
        app.sla_scheduler = scheduler
        log.info("[SLA Monitor] Scheduler démarré (surveillance 5min)")
    except Exception as exc:
        log.error("[SLA Monitor] Impossible de démarrer : %s", exc)
