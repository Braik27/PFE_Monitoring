from __future__ import annotations
import logging, os, threading, uuid, time
from urllib.parse import quote
from engine.pipeline import AnalysisResult

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = logging.getLogger(__name__)


# ─── Grouped rafale notifications (30s buffer) ──────────────────────────
_alert_buffer: list[dict] = []
_buffer_lock = threading.Lock()
_BUFFER_FLUSH_SECONDS = 30


def _flush_buffer():
    """Send grouped rafale notification for all buffered alerts."""
    with _buffer_lock:
        if not _alert_buffer:
            return
        alerts = list(_alert_buffer)
        _alert_buffer.clear()

    if len(alerts) == 1:
        a = alerts[0]
        _send_single_rafale(a["flux_name"], a["token"], a["n_critiques"], a.get("severity", ""))
        return

    base = os.environ.get("APP_BASE_URL", "")
    flux_summary = {}
    for a in alerts:
        fn = a["flux_name"]
        flux_summary.setdefault(fn, {"count": 0, "total": 0, "tokens": []})
        flux_summary[fn]["count"] += 1
        flux_summary[fn]["total"] += a["n_critiques"]
        flux_summary[fn]["tokens"].append(a["token"][:12])

    items_html = ""
    for fn, info in flux_summary.items():
        tokens_preview = ", ".join(info["tokens"][:5])
        if info["count"] > 1:
            tokens_preview += f" (+{info['count'] - 5} de plus)" if info["count"] > 5 else ""
        items_html += f"""
        <tr>
          <td style='padding:8px 12px;border-bottom:1px solid #e5e7eb;font-weight:600'>{fn}</td>
          <td style='padding:8px 12px;border-bottom:1px solid #e5e7eb;text-align:center;color:#dc2626;font-weight:700'>{info["total"]}</td>
          <td style='padding:8px 12px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#64748b'>{tokens_preview}</td>
        </tr>"""

    to_addr = os.environ.get("ALERT_EMAIL_TO", "")
    from_addr = os.environ.get("ALERT_EMAIL_FROM", os.environ.get("ALERT_SMTP_USER", "noreply@fluxmonitor.timsoft.com"))
    if not to_addr:
        return

    body = f"""<html><body style='font-family:Arial,sans-serif;background:#f8fafc;padding:20px'>
<div style='max-width:700px;margin:0 auto;background:#fff;border-radius:12px;padding:30px;border:1px solid #dde3f0'>
  <h2 style='color:#dc2626;margin-bottom:4px'>🚨 Rafale : {len(alerts)} alertes détectées</h2>
  <p style='color:#64748b;font-size:13px;margin-bottom:16px'>Résumé groupé — dernières 30 secondes</p>
  <table style='width:100%;border-collapse:collapse;font-size:13px'>
    <tr style='background:#f1f5f9'>
      <th style='padding:8px 12px;text-align:left;border-bottom:2px solid #cbd5e1'>Flux</th>
      <th style='padding:8px 12px;text-align:center;border-bottom:2px solid #cbd5e1'>Total critiques</th>
      <th style='padding:8px 12px;text-align:left;border-bottom:2px solid #cbd5e1'>Tokens</th>
    </tr>
    {items_html}
  </table>
  <div style='margin-top:20px;padding:14px;background:#f1f5f9;border-radius:8px;font-size:12px;color:#64748b'>
    <strong>Flux Monitor — TimSoft</strong><br>
    <a href='{base}/' style='color:#1d4ed8'>Ouvrir le dashboard →</a>
  </div>
</div></body></html>"""

    subject = f"🚨 Rafale — {len(alerts)} alertes ({sum(a['n_critiques'] for a in alerts)} critiques)"
    _send_via_smtp(from_addr, to_addr, subject, body)
    log.info("[Rafale] Grouped notification sent: %d alerts", len(alerts))


def _send_single_rafale(flux_name: str, token: str, n_critiques: int, severity: str = ""):
    """Fallback: single alert rafale."""
    base = os.environ.get("APP_BASE_URL", "")
    to_addr = os.environ.get("ALERT_EMAIL_TO", "")
    from_addr = os.environ.get("ALERT_EMAIL_FROM", os.environ.get("ALERT_SMTP_USER", "noreply@fluxmonitor.timsoft.com"))
    if not to_addr:
        return

    sev_label = f" — {severity}" if severity else ""
    body = f"""<html><body style='font-family:Arial,sans-serif;background:#f8fafc;padding:20px'>
<div style='max-width:600px;margin:0 auto;background:#fff;border-radius:10px;padding:30px;border:1px solid #dde3f0'>
  <h2 style='color:#dc2626'>🚨 Nouvelle alerte{sev_label}</h2>
  <p><b>Flux :</b> {flux_name}</p>
  <p><b>Critiques :</b> {n_critiques}</p>
  <p><a href='{base}/alerts?token={token}' style='background:#dc2626;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:700'>Voir l'alerte</a></p>
</div></body></html>"""

    subject = f"🚨 Alerte {flux_name}{sev_label} — {n_critiques} critiques"
    _send_via_smtp(from_addr, to_addr, subject, body)


def _schedule_flush():
    """Start a background timer to flush the buffer after 30s."""
    def _timer_fn():
        time.sleep(_BUFFER_FLUSH_SECONDS)
        _flush_buffer()
    t = threading.Thread(target=_timer_fn, daemon=True)
    t.start()


def send_alert_async(result: AnalysisResult, analysis_id: int = 0):
    """Lance l'envoi dans un thread séparé — non bloquant."""
    threading.Thread(target=_send, args=(result, analysis_id), daemon=True).start()


def send_missing_file_alert_async(
    flux_id: str,
    flux_name: str,
    label: str,
    token: str,
    expected_hour: str = "",
):
    """Notification email pour alerte FICHIER_MANQUANT (watcher / manual)."""
    threading.Thread(
        target=_send_missing_file,
        args=(flux_id, flux_name, label, token, expected_hour),
        daemon=True,
    ).start()


def _send_missing_file(flux_id, flux_name, label, token, expected_hour):
    if os.environ.get("ALERT_EMAIL_ENABLED", "false").lower() != "true":
        log.info("[MISSING-FILE] Email désactivé (ALERT_EMAIL_ENABLED)")
        return

    to_addr = os.environ.get("ALERT_EMAIL_TO", "")
    if not to_addr:
        log.warning("[MISSING-FILE] ALERT_EMAIL_TO non configuré")
        return

    base_url = os.environ.get(
        "APP_BASE_URL",
        "https://flask-trainer-app-f8bpdvavegh2gjh2.francecentral-01.azurewebsites.net",
    )
    ack_url = f"{base_url}/alert/{token}/ack"
    ignore_url = f"{base_url}/alert/{token}/ignore"

    subject = f"🚨 [FICHIER MANQUANT] {flux_name} — {flux_id}"
    body = f"""<html><body style='font-family:Arial,sans-serif;background:#fff5f5;padding:20px'>
<div style='max-width:600px;margin:0 auto;background:#fff;border-radius:10px;padding:30px;border:1px solid #feb2b2'>
  <h2 style='color:#e53e3e'>🚨 Fichier attendu manquant</h2>
  <p><b>Flux :</b> {flux_name} ({flux_id})</p>
  <p><b>Description :</b> {label}</p>
  <p><b>Heure limite :</b> {expected_hour or '—'}</p>
  <p style='color:#64748b;font-size:13px'>Token : <code>{token[:12]}…</code></p>
  <div style='margin:20px 0'>
    <a href='{ack_url}' style='background:#059669;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:700;margin-right:8px'>✅ Prendre en charge</a>
    <a href='{ignore_url}' style='background:#dc2626;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:700'>🚫 Ignorer</a>
  </div>
  <p style='font-size:12px;color:#718096'>Flux Monitor — TimSoft</p>
</div></body></html>"""

    from_addr = os.environ.get("ALERT_EMAIL_FROM", os.environ.get("ALERT_SMTP_USER", "noreply@fluxmonitor.timsoft.com"))
    use_sendgrid = os.environ.get("ALERT_PROVIDER", "smtp").lower() == "sendgrid"
    if use_sendgrid:
        _send_via_sendgrid(from_addr, to_addr, subject, body)
    else:
        _send_via_smtp(from_addr, to_addr, subject, body)


def _should_send(result: AnalysisResult) -> bool:
    if os.environ.get("ALERT_EMAIL_ENABLED", "false").lower() != "true":
        return False
    min_crit = int(os.environ.get("ALERT_MIN_CRITIQUES", "1"))
    return result.total_anomalies > 0 if min_crit == 0 else result.total_critiques >= min_crit


def _build_email_content(result: AnalysisResult, token: str, base_url: str):
    icon    = "🚨" if result.total_critiques > 0 else "⚠️"
    subject = (f"{icon} [{result.flux_name}] {result.total_critiques} critique(s), "
               f"{result.total_warnings} warning(s) — {result.label}")

    rows_html = ""
    for pair in result.pairs:
        for a in pair.anomalies[:50]:
            color   = "#ef4444" if a.severity == "CRITIQUE" else "#f59e0b"
            key_str = " | ".join(f"{k}={v}" for k, v in a.key_values.items())
            rows_html += (
                f"<tr><td style='color:{color};font-weight:bold'>{a.severity}</td>"
                f"<td>{a.error_type}</td>"
                f"<td style='font-family:monospace;font-size:11px'>{key_str}</td>"
                f"<td>{a.val_cegid or ''}</td><td>{a.val_oracle or ''}</td>"
                f"<td>{a.explication}</td></tr>"
            )

    # ✅ URLs directes vers les actions - /login redirigera si necessaire
    # - "Je prends en charge" -> /alert/{token}/ack -> login si non connecte
    # - "Ignorer" -> /alert/{token}/ignore -> login si non connecte
    # - "Ouvrir le dashboard" -> /
    ack_url       = f"{base_url}/alert/{token}/ack"
    ignore_url    = f"{base_url}/alert/{token}/ignore"
    dashboard_url = f"{base_url}/"

    body = f"""<html><body style='font-family:Arial,sans-serif;background:#f8fafc;padding:20px'>
<div style='max-width:900px;margin:0 auto;background:#fff;border-radius:10px;padding:30px;border:1px solid #dde3f0'>
  <h2 style='color:#1e40af;margin-bottom:8px'>🔍 Flux Monitor — Alerte {result.flux_name}</h2>
  <p style='color:#64748b;font-size:13px;margin-bottom:16px'>
    Token : <code style='background:#f1f5f9;padding:2px 6px;border-radius:4px'>{token}</code>
    &nbsp;|&nbsp; <strong style='color:#d97706'>⚠️ SLA: À traiter sous 4h</strong>
  </p>
  <div style='background:#f8fafc;border-radius:8px;padding:14px;margin-bottom:20px'>
    <p><b>📋 {result.label}</b></p>
    <div style='display:flex;gap:16px;margin-top:10px;flex-wrap:wrap'>
      <div style='background:#ecfdf5;padding:8px 14px;border-radius:6px'>
        <div style='font-size:11px;color:#059669;font-weight:600'>CONCORDANCE</div>
        <div style='font-size:22px;font-weight:800;color:#059669'>{result.concordance_moyenne}%</div>
      </div>
      <div style='background:#fef2f2;padding:8px 14px;border-radius:6px'>
        <div style='font-size:11px;color:#dc2626;font-weight:600'>CRITIQUES</div>
        <div style='font-size:22px;font-weight:800;color:#dc2626'>{result.total_critiques}</div>
      </div>
      <div style='background:#fffbeb;padding:8px 14px;border-radius:6px'>
        <div style='font-size:11px;color:#d97706;font-weight:600'>WARNINGS</div>
        <div style='font-size:22px;font-weight:800;color:#d97706'>{result.total_warnings}</div>
      </div>
    </div>
  </div>

  <div style='background:linear-gradient(135deg,#fef3c7,#fff7ed);border:1px solid:#fcd34d;border-radius:8px;padding:16px;margin-bottom:20px'>
    <h3 style='margin:0 0 6px 0;color:#92400e;font-size:14px'>⚡ Actions rapides</h3>
    <p style='font-size:12px;color:#92400e;margin:0 0 12px 0'>🔒 Connexion requise — vous serez redirigé automatiquement après authentification.</p>
    <div style='display:flex;gap:10px;flex-wrap:wrap'>
      <a href='{ack_url}'    style='background:#059669;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px'>✅ Je prends en charge [{token}]</a>
      <a href='{ignore_url}' style='background:#dc2626;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px'>🚫 Ignorer [{token}]</a>
    </div>
  </div>

  <table style='width:100%;border-collapse:collapse;font-size:12px'>
    <thead><tr style='background:#1e40af;color:#fff'>
      <th style='padding:8px;text-align:left'>Sévérité</th>
      <th style='padding:8px;text-align:left'>Type d'erreur</th>
      <th style='padding:8px;text-align:left'>Clé</th>
      <th style='padding:8px;text-align:left'>Cegid</th>
      <th style='padding:8px;text-align:left'>Oracle</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>

  <div style='margin-top:24px;padding:14px;background:#f1f5f9;border-radius:8px;font-size:12px;color:#64748b'>
    <p><strong>Flux Monitor — TimSoft</strong></p>
    <p>Cet email a été envoyé automatiquement suite à une analyse de réconciliation.</p>
    <p style='margin-top:8px'><a href='{dashboard_url}' style='color:#1d4ed8'>Ouvrir le dashboard →</a></p>
  </div>
</div>
</body></html>"""

    return subject, body


def _send_via_smtp(from_addr: str, to_addr: str, subject: str, body: str):
    if not os.environ.get("ALERT_SMTP_USER") or not os.environ.get("ALERT_SMTP_PASSWORD"):
        log.error("SMTP credentials not configured")
        return False

    from core.email_service import send_email

    return send_email(to_addr, subject, body, from_addr=from_addr)


def _send_via_sendgrid(from_addr: str, to_addr: str, subject: str, body: str):
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, Email, To, Content
    except ImportError:
        log.error("SendGrid non installé. Exécutez: pip install sendgrid")
        return False

    sendgrid_key = os.environ.get("SENDGRID_API_KEY", "")
    if not sendgrid_key:
        log.error("SENDGRID_API_KEY non configuré")
        return False

    try:
        sg = SendGridAPIClient(sendgrid_key)
        message = Mail(
            from_email=Email(from_addr),
            to_emails=To(to_addr),
            subject=subject,
            html_content=Content("text/html", body)
        )
        response = sg.send(message)
        log.info("Email SendGrid envoyé → %s (status: %s)", to_addr, response.status_code)
        return True
    except Exception as e:
        log.error("Erreur SendGrid: %s", e)
        return False


def _send(result: AnalysisResult, analysis_id: int = 0):
    """
    Crée l'alerte et broadcast via WebSocket.
    AUCUN EMAIL n'est envoyé suite à une comparaison.
    Les emails sont réservés à : SLA proche, SLA dépassée, escalade, ignorée.
    """
    token = uuid.uuid4().hex

    all_anomalies = []
    for pair in result.pairs:
        for a in pair.anomalies[:200]:
            all_anomalies.append({
                "severity":   a.severity,
                "error_type": a.error_type,
                "key_values": a.key_values,
                "val_cegid":  a.val_cegid or "",
                "val_oracle": a.val_oracle or "",
                "explication": a.explication,
                "action":     a.action,
            })

    if result.total_anomalies == 0:
        return

    from datetime import datetime
    from core.sla_policy import build_sla_meta, get_expected_hour_for_flux

    comparison_stats = {}
    for pair in result.pairs:
        comparison_stats.setdefault("nb_lignes_cegid", 0)
        comparison_stats["nb_lignes_cegid"] += getattr(pair, "n_cegid", 0) or 0
        comparison_stats.setdefault("nb_lignes_oracle", 0)
        comparison_stats["nb_lignes_oracle"] += getattr(pair, "n_oracle", 0) or 0

    expected_hour = get_expected_hour_for_flux(result.flux_id)
    detected_at = datetime.utcnow()
    sla_meta = build_sla_meta(
        all_anomalies,
        n_critiques=result.total_critiques,
        n_warnings=result.total_warnings,
        concordance=result.concordance_moyenne,
        comparison_stats=comparison_stats,
        expected_hour=expected_hour,
        detected_at=detected_at,
    )

    try:
        from storage import get_storage
        get_storage().save_alert(
            token=token,
            analysis_id=analysis_id,
            flux_id=result.flux_id,
            flux_name=result.flux_name,
            label=result.label,
            n_critiques=result.total_critiques,
            n_warnings=result.total_warnings,
            concordance=result.concordance_moyenne,
            anomalies=all_anomalies,
            email_sent_to="",
            sla_meta=sla_meta,
            workflow_status="NEW",
            severity=sla_meta.get("severity", ""),
        )
        log.info("Alerte sauvegardée — token=%s, severity=%s, concordance_state=%s",
                 token, sla_meta.get("severity", ""), sla_meta.get("concordance_state", ""))

        try:
            from app import app as _app
            broadcast = getattr(_app, 'broadcast_new_alert', None)
            if broadcast:
                broadcast(
                    result.flux_name,
                    token,
                    result.total_critiques,
                    severity=sla_meta.get("severity", ""),
                    sla_status="ON_TIME",
                )
        except Exception as _be:
            log.debug("WS broadcast skipped: %s", _be)
    except Exception as e:
        log.error("Erreur sauvegarde alerte: %s", e)

    log.info("Comparaison terminée — token=%s — concordance=%s%% — AUCUN email envoyé",
             token, result.concordance_moyenne)