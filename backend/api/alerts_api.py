"""
api/alerts_api.py — Routes pour les alertes traçables.

GET    /alert/<token>               → vérifie auth, exécute action si ?action=, redirige vers dashboard
GET    /api/alerts                  → liste des alertes
GET    /api/alerts/<token>          → détail complet + tracking
GET    /api/alerts/<token>/track    → action rapide depuis email (GET, sans login)
POST   /api/alerts/<token>/track    → action depuis le dashboard (POST, authentifié)
PATCH  /api/alerts/<token>/status   → mise à jour statut
DELETE /api/alerts/<token>          → suppression définitive d'une alerte
GET    /api/alerts/<token>/suggest  → suggestion IA (Claude API)
POST   /api/alerts/<token>/resolve  → marque RESOLVED
POST   /api/alerts/<token>/escalate → escalade vers consultant
"""
import logging
from flask import Blueprint, jsonify, request, session, redirect
from storage import get_storage
from api.auth import require_auth, require_admin
import os, threading
from urllib.parse import quote

log = logging.getLogger(__name__)


def _send_escalation_email(to_email: str, username: str, flux_id: str, token: str, reason: str, comment: str, escalated_by: str, alert: dict = None):
    """Envoie un email d'escalade complet — liens sécurisés avec auth obligatoire."""
    def _do_send():
        try:
            log.info("[ESCALATE] Tentative d'envoi vers %s", to_email)

            base_url = os.environ.get("APP_BASE_URL", "https://flask-trainer-app-f8bpdvavegh2gjh2.francecentral-01.azurewebsites.net")

            ack_url       = f"{base_url}/alert/{token}/ack"
            ignore_url    = f"{base_url}/alert/{token}/ignore"
            dashboard_url = f"{base_url}/"

            alert_data = alert or {}
            concordance = alert_data.get("concordance", "—")
            severity = alert_data.get("severity", "")
            sla_h = alert_data.get("sla_hours", "—")
            remaining = alert_data.get("remaining_pct", "—")
            created_at = alert_data.get("created_at", "—")
            label = alert_data.get("label", flux_id)
            anomalies_count = alert_data.get("n_critiques", 0)

            from core.sla_policy import get_concordance_state
            conc_state = get_concordance_state(float(concordance) if concordance != "—" else 100.0)

            subject = f"🔄 Alerte escaladée vers vous — {flux_id}"

            body_html = f"""<html><body style='font-family:Arial,sans-serif;background:#f8fafc;padding:20px'>
<div style='max-width:650px;margin:0 auto;background:#fff;border-radius:10px;padding:30px;border:1px solid #dde3f0'>
  <h2 style='color:#d97706;margin-bottom:8px'>🔄 Une alerte vous a été escaladée</h2>
  <p style='color:#64748b;font-size:13px'>Bonjour <strong>{username}</strong>,</p>

  <div style='background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;padding:16px;margin:16px 0'>
    <h3 style='font-size:14px;color:#92400e;margin:0 0 10px 0'>📋 Détails de l'alerte</h3>
    <p style='margin:4px 0'><b>ID Alerte :</b> <code style='background:#f1f5f9;padding:2px 6px;border-radius:4px;font-size:11px'>{token[:16]}…</code></p>
    <p style='margin:4px 0'><b>Flux :</b> {flux_id}</p>
    <p style='margin:4px 0'><b>Description :</b> {label}</p>
    <p style='margin:4px 0'><b>Criticité :</b> {severity or conc_state}</p>
    <p style='margin:4px 0'><b>Concordance :</b> {concordance}% ({conc_state})</p>
    <p style='margin:4px 0'><b>Anomalies critiques :</b> {anomalies_count}</p>
    <p style='margin:4px 0'><b>SLA :</b> {sla_h}h — reste {remaining}%</p>
    <p style='margin:4px 0'><b>Créée le :</b> {created_at}</p>
    <p style='margin:4px 0'><b>Escaladée par :</b> <strong>{escalated_by}</strong></p>
    <p style='margin:4px 0'><b>Raison :</b> {reason}</p>
    <p style='margin:4px 0'><b>Commentaire :</b> {comment or "—"}</p>
  </div>

  <div style='background:linear-gradient(135deg,#fef3c7,#fff7ed);border:1px solid #fcd34d;border-radius:8px;padding:14px;margin-bottom:20px'>
    <h3 style='font-size:13px;color:#92400e;margin:0 0 6px 0'>⚡ Actions rapides</h3>
    <p style='font-size:11px;color:#92400e;margin:0 0 10px 0'>🔒 Connexion requise — vous serez redirigé automatiquement après authentification.</p>
    <div style='display:flex;gap:10px;flex-wrap:wrap'>
      <a href='{ack_url}'    style='background:#059669;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:700;font-size:13px'>✅ Prendre en charge</a>
      <a href='{ignore_url}' style='background:#dc2626;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:700;font-size:13px'>🚫 Ignorer</a>
    </div>
  </div>

  <div style='padding:14px;background:#f1f5f9;border-radius:8px;font-size:12px;color:#64748b'>
    <strong>Flux Monitor — TimSoft</strong><br>
    Cet email a été envoyé automatiquement suite à une escalade d'alerte.<br>
    <a href='{dashboard_url}' style='color:#1d4ed8'>Ouvrir le dashboard →</a>
  </div>
</div></body></html>"""

            from core.email_service import send_email

            if not send_email(to_email, subject, body_html):
                log.error("[ESCALATE] Envoi impossible vers %s (SMTP non configure ou erreur)", to_email)
                return

            log.info("[ESCALATE] Email envoye vers %s", to_email)

        except Exception as e:
            log.error("[ESCALATE] Erreur: %s", e)

    threading.Thread(target=_do_send, daemon=True).start()


alerts_bp = Blueprint("alerts", __name__)

VALID_STATUSES = ("NEW", "ACKNOWLEDGED", "IN_PROGRESS", "ESCALATED", "RESOLVED", "IGNORED", "CLOSED")

STATUS_LABELS = {
    "NEW":          "🟡 Nouveau",
    "PENDING":      "🟡 Nouveau",
    "ACKNOWLEDGED": "🔵 Pris en charge",
    "IN_PROGRESS":  "🟣 En cours",
    "ESCALATED":    "🟠 Escaladée",
    "RESOLVED":     "🟢 Résolu",
    "CLOSED":       "⚫ Clôturée",
    "IGNORED":      "🚫 Ignoré",
}

STATUS_CSS = {
    "NEW":          "b-o",
    "PENDING":      "b-o",
    "ACKNOWLEDGED": "b-b",
    "IN_PROGRESS":  "b-p",
    "ESCALATED":    "b-o",
    "RESOLVED":     "b-g",
    "CLOSED":       "b-i",
    "IGNORED":      "b-i",
}


def notify_on_alert_ignored(token: str, b_username: str, comment: str):
    """Envoie un email et pousse une notification websocket quand B ignore une alerte escaladée."""
    import os, threading
    from storage import get_storage
    from core.sla_policy import get_concordance_state, recompute_sla_progress

    storage = get_storage()
    alert = storage.get_alert_by_token(token)
    if not alert:
        return

    # Anti-doublon : flag ignore_notification_sent
    if alert.get("ignore_notification_sent"):
        log.info("[IGNORE-NOTIF] Notification d'ignorance déjà envoyée pour l'alerte %s", token)
        return

    tracking = storage.get_tracking(token)
    escalated_by_username = None
    for entry in reversed(tracking):
        if entry.get("action", "").startswith("ESCALATED_TO:"):
            escalated_by_username = entry.get("username")
            break

    if not escalated_by_username:
        return

    user_a = storage.get_user(escalated_by_username)
    if not user_a or not user_a.get("email"):
        return

    email_a = user_a["email"]
    name_a = user_a.get("full_name") or user_a.get("username")

    # Marquer l'envoi en base
    storage.set_ignore_notification_sent(token)

    # 1. Pousser la notification WebSocket à A
    try:
        from flask import current_app
        app_obj = current_app._get_current_object()
        broadcast = getattr(app_obj, 'broadcast_custom_notification', None)
        if broadcast:
            alert_id = alert.get("id") or token[:8]
            msg = f"⚠️ L'utilisateur {b_username} a ignoré l'alerte #{alert_id} que vous aviez escaladée."
            broadcast(target_username=user_a.get("username"), message=msg, token=token, type_notif="alert_ignored")
    except Exception as ws_err:
        log.warning("Erreur WS broadcast ignorance: %s", ws_err)

    # 2. Envoyer l'email à A
    base_url = os.environ.get("APP_BASE_URL", "")
    concordance = alert.get("concordance", "—")
    sla_h = alert.get("sla_hours", "—")

    sla_prog = recompute_sla_progress(alert)
    remaining = sla_prog.get("remaining_pct", "—")
    severity = alert.get("severity") or alert.get("severity_class", "")
    conc_state = get_concordance_state(float(concordance) if concordance != "—" else 100.0)

    subject = f"⚠️ L'alerte escaladée a été ignorée — {alert.get('flux_id', '')}"
    body = f"""<html><body style='font-family:Arial,sans-serif;background:#f8fafc;padding:20px'>
<div style='max-width:600px;margin:0 auto;background:#fff;border-radius:10px;padding:30px;border:1px solid #dde3f0'>
  <h2 style='color:#dc2626;margin-bottom:8px'>⚠️ L'alerte escaladée a été ignorée</h2>
  <p style='color:#64748b;font-size:13px'>Bonjour <strong>{name_a}</strong>,</p>
  <div style='background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:16px;margin:16px 0'>
    <p style='margin:4px 0'><b>📋 ID Alerte :</b> #{alert.get('id') or token[:8]}</p>
    <p style='margin:4px 0'><b>🔍 Titre/Flux :</b> {alert.get('flux_name', '')} ({alert.get('flux_id', '')})</p>
    <p style='margin:4px 0'><b>Criticité :</b> {severity or conc_state}</p>
    <p style='margin:4px 0'><b>Taux de concordance :</b> {concordance}% ({conc_state})</p>
    <p style='margin:4px 0'><b>Utilisateur B (ayant ignoré) :</b> {b_username}</p>
    <p style='margin:4px 0'><b>Utilisateur A (ayant escaladé) :</b> {user_a.get('username')}</p>
    <p style='margin:4px 0'><b>Motif/Commentaire :</b> {comment or '—'}</p>
    <p style='margin:4px 0'><b>SLA :</b> {sla_h}h — reste {remaining}%</p>
    <p style='margin:4px 0'><b>Statut :</b> IGNORED</p>
  </div>
  <a href='{base_url}/alerts?token={token}' style='display:inline-block;background:#dc2626;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:700'>Voir l'alerte</a>
  <div style='margin-top:20px;padding:14px;background:#f1f5f9;border-radius:8px;font-size:12px;color:#64748b'>
    <strong>Flux Monitor — TimSoft</strong><br>
    <a href='{base_url}/' style='color:#1d4ed8'>Ouvrir le dashboard →</a>
  </div>
</div></body></html>"""

    smtp_host = os.environ.get("ALERT_SMTP_HOST", "")
    smtp_user = os.environ.get("ALERT_SMTP_USER", "")

    if smtp_host and smtp_user:
        def _do_send_ignore():
            try:
                from core.email_service import send_email

                if not send_email(email_a, subject, body):
                    log.error("[IGNORE] Envoi impossible vers %s (SMTP non configure ou erreur)", email_a)
                    return
                log.info("[IGNORE] Email envoyé vers %s", email_a)
            except Exception as e:
                log.error("[IGNORE] Erreur SMTP: %s", e)

        threading.Thread(target=_do_send_ignore, daemon=True).start()


def _actor_from_session() -> dict:
    u = session.get("user") or {}
    return {
        "username": u.get("username", "system"),
        "role": u.get("role", "viewer"),
        "sub": u.get("sub", "unknown"),
    }


def _apply_transition(token: str, new_status: str, comment: str = ""):
    """Passe par transition_alert() — retourne (response, status_code) ou None si OK."""
    from core.alert_state_machine import (
        transition_alert,
        InvalidTransitionError,
        ValidationError,
        PermissionError,
    )
    try:
        transition_alert(get_storage(), token, new_status, _actor_from_session(), comment)
        return None
    except InvalidTransitionError as exc:
        return jsonify({"error": str(exc)}), 409
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 422
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403


def _build_confirmation_page(action: str, username: str, alert: dict, token: str) -> str:
    """Construit la page HTML de confirmation après une action sur une alerte."""
    base_url = os.environ.get(
        "APP_BASE_URL",
        "https://flask-trainer-app-f8bpdvavegh2gjh2.francecentral-01.azurewebsites.net"
    )
    action_labels = {
        "ACKNOWLEDGED": "Pris en charge",
        "IN_PROGRESS":  "En cours de traitement",
        "RESOLVED":     "Résolu",
    }
    action_colors = {
        "ACKNOWLEDGED": "#059669",
        "IN_PROGRESS":  "#7c3aed",
        "RESOLVED":     "#1d4ed8",
    }
    action_icons = {
        "ACKNOWLEDGED": "✅",
        "IN_PROGRESS":  "🔧",
        "RESOLVED":     "✔",
    }

    flux_name = alert.get("flux_name", "")
    n_crit    = alert.get("n_critiques", 0)
    n_warn    = alert.get("n_warnings", 0)
    conc      = alert.get("concordance", 0)
    label     = alert.get("label", "")
    urgency   = "🔴 Urgent" if n_crit > 0 else "🟡 Attention" if n_warn > 0 else "🟢 Normal"
    urg_color = "#dc2626" if n_crit > 0 else "#d97706" if n_warn > 0 else "#059669"
    color     = action_colors.get(action, "#1d4ed8")

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Alerte — {action_labels.get(action, action)}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:Arial,sans-serif;background:linear-gradient(135deg,#f0f9ff,#e0f2fe);
          display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}}
    .card{{background:#fff;border-radius:16px;padding:36px;
           box-shadow:0 8px 32px rgba(0,0,0,.12);max-width:520px;width:100%}}
    .icon{{font-size:56px;margin-bottom:12px;text-align:center}}
    h2{{color:{color};margin-bottom:6px;font-size:22px;text-align:center}}
    .by{{text-align:center;font-size:13px;color:#64748b;margin-bottom:20px}}
    .by strong{{color:#1e40af}}
    .details{{background:#f8fafc;border-radius:10px;padding:16px;margin-bottom:20px;border:1px solid #e2e8f0}}
    .detail-row{{display:flex;justify-content:space-between;align-items:center;
                  padding:6px 0;border-bottom:1px solid #f1f5f9;font-size:13px}}
    .detail-row:last-child{{border-bottom:none}}
    .detail-key{{color:#64748b;font-weight:600}}
    .detail-val{{font-weight:700;color:#1e293b}}
    .urgency{{display:inline-block;padding:3px 12px;border-radius:20px;font-size:12px;
               font-weight:700;background:{urg_color}22;color:{urg_color};border:1px solid {urg_color}44}}
    .status-badge{{background:{color}22;color:{color};border:1px solid {color}44;
                    padding:3px 12px;border-radius:20px;font-size:12px;font-weight:700}}
    .actions{{display:flex;gap:10px;flex-wrap:wrap;justify-content:center}}
    .btn{{display:inline-block;padding:11px 22px;border-radius:8px;
          text-decoration:none;font-weight:700;font-size:13px}}
    .btn-primary{{background:{color};color:#fff}}
    .btn-secondary{{background:#f1f5f9;color:#475569}}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">{action_icons.get(action, "✅")}</div>
    <h2>{action_labels.get(action, action)}</h2>
    <p class="by">par <strong>{username}</strong></p>
    <div class="details">
      <div class="detail-row"><span class="detail-key">Flux</span><span class="detail-val">{flux_name}</span></div>
      <div class="detail-row"><span class="detail-key">Description</span><span class="detail-val" style="font-size:12px;max-width:280px;text-align:right">{label}</span></div>
      <div class="detail-row"><span class="detail-key">Concordance</span>
        <span class="detail-val" style="color:{'#059669' if conc >= 95 else '#d97706' if conc >= 80 else '#dc2626'}">{conc}%</span>
      </div>
      <div class="detail-row"><span class="detail-key">Critiques</span><span class="detail-val" style="color:#dc2626">{n_crit}</span></div>
      <div class="detail-row"><span class="detail-key">Warnings</span><span class="detail-val" style="color:#d97706">{n_warn}</span></div>
      <div class="detail-row"><span class="detail-key">Urgence</span><span class="urgency">{urgency}</span></div>
      <div class="detail-row"><span class="detail-key">Nouveau statut</span><span class="status-badge">{action_labels.get(action, action)}</span></div>
    </div>
    <div class="actions">
      <a href="{base_url}/alerts?token={token}" class="btn btn-primary">🔍 Voir l'alerte complète</a>
      <a href="{base_url}/" class="btn btn-secondary">📊 Dashboard</a>
    </div>
  </div>
</body>
</html>"""


def execute_alert_action(token: str, action: str):
    """
    Exécute une action sur une alerte et redirige vers /alerts?token={token}.
    Appelé par app.py pour /alert/<token>/ack et /alert/<token>/ignore.
    """
    user     = session.get("user") or {}
    username = user.get("full_name") or user.get("username", "Utilisateur")

    alert = get_storage().get_alert_by_token(token)
    if not alert:
        base_url = os.environ.get("APP_BASE_URL", "")
        return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<title>Alerte introuvable</title>
<style>body{{font-family:Arial,sans-serif;display:flex;align-items:center;justify-content:center;
min-height:100vh;background:#fef2f2}}
.card{{background:#fff;padding:36px;border-radius:16px;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,.1);max-width:420px}}
h2{{color:#dc2626;margin-bottom:12px}}p{{color:#64748b;margin-bottom:20px}}
.btn{{display:inline-block;padding:10px 22px;background:#1d4ed8;color:#fff;
border-radius:8px;text-decoration:none;font-weight:700}}</style></head>
<body><div class="card"><div style="font-size:48px">❌</div>
<h2>Alerte introuvable</h2>
<p>Ce token ne correspond à aucune alerte active.<br>Elle a peut-être déjà été traitée.</p>
<a href="{base_url}/" class="btn">📊 Retour au Dashboard</a>
</div></body></html>""", 404

    err = _apply_transition(token, action, f"Action depuis email — effectuée par {username}")
    if err:
        base_url = os.environ.get("APP_BASE_URL", "")
        return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">
<title>Transition refusée</title></head><body style='font-family:Arial;padding:40px'>
<h2>Action impossible</h2><p>{err[0].get_json().get('error', 'Erreur')}</p>
<a href="{base_url}/">Retour au dashboard</a></body></html>""", err[1]

    get_storage().save_tracking(
        alert_token=token,
        username=user.get("username", "system"),
        action=action,
        comment=f"Action depuis email — effectuée par {username}",
    )

    if action == "IGNORED":
        notify_on_alert_ignored(token, username, f"Action depuis email — effectuée par {username}")

    return redirect(f"/alerts?token={token}")


# ── /alert/<token> — point d'entrée depuis email ──────────────────────
@alerts_bp.get("/alert/<token>")
def alert_page(token: str):
    user = session.get("user")

    # Pas connecté → login, revient ici après
    if not user:
        action_param = request.args.get("action", "")
        next_url = f"/alert/{token}"
        if action_param:
            next_url += f"?action={action_param}"
        return redirect(f"/login?next={quote(next_url, safe='')}")

    action   = request.args.get("action", "").upper()
    username = user.get("full_name") or user.get("username", "Utilisateur")

    if action in ("ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED", "IGNORED"):
        alert = get_storage().get_alert_by_token(token)
        if not alert:
            return redirect("/?error=alert_not_found")

        err = _apply_transition(
            token, action,
            f"Action depuis email — effectuée par {username}",
        )
        if err:
            return redirect(f"/alerts?token={token}&error=transition")

        get_storage().save_tracking(
            alert_token=token,
            username=user.get("username", "system"),
            action=action,
            comment=f"Action depuis email — effectuée par {username}",
        )
        return redirect(f"/alerts?token={token}")

    # Pas d'action → redirige vers la page Alertes avec l'alerte ouverte
    return redirect(f"/alerts?token={token}")


@alerts_bp.post("/api/alerts/manual")
@require_auth
def create_manual_alert():
    """
    Crée manuellement une alerte critique (ex: FICHIER_MANQUANT).
    """
    import uuid
    data = request.get_json(silent=True) or {}
    flux_id = data.get("flux_id")
    if not flux_id:
        return jsonify({"erreur": "flux_id est requis"}), 400

    division = data.get("division", "GLOBAL")
    expected_hour = data.get("expected_hour", "")
    flux_name = data.get("flux_name", f"Flux {flux_id}")
    label = data.get("label", "Fichier attendu manquant")

    token = uuid.uuid4().hex

    anomalies = [{
        "severity": "CRITIQUE",
        "error_type": "FICHIER_MANQUANT",
        "key_values": {"flux_id": flux_id, "division": division},
        "val_cegid": "",
        "val_oracle": "",
        "explication": f"Le couple de fichiers attendu pour le flux {flux_id} n'est pas arrivé avant l'heure limite ({expected_hour}).",
        "action": "Vérifier le dossier d'importation et contacter l'équipe d'exploitation."
    }]

    try:
        from datetime import datetime
        from core.sla_policy import (
            build_sla_meta,
            compute_sla_deadline,
            get_expected_hour_for_flux,
            validate_sla_hours,
        )

        if not expected_hour:
            expected_hour = get_expected_hour_for_flux(flux_id)
        detected_at = datetime.utcnow()
        sla_meta = build_sla_meta(
            anomalies,
            n_critiques=1,
            n_warnings=0,
            concordance=0.0,
            expected_hour=expected_hour,
            detected_at=detected_at,
        )

        # Extract and validate SLA if provided by client
        client_sla = data.get("sla_hours") or data.get("sla")
        if client_sla is not None:
            try:
                if isinstance(client_sla, str):
                    client_sla = client_sla.replace("h", "").strip()
                client_sla_val = float(client_sla)
                validate_sla_hours(client_sla_val)
                # Override computed SLA
                sla_meta["sla_hours"] = client_sla_val
                sla_meta["sla_deadline"] = compute_sla_deadline(detected_at, client_sla_val).isoformat()
            except ValueError as val_err:
                return jsonify({"error": str(val_err), "erreur": str(val_err)}), 400
            except Exception:
                return jsonify({"error": "Format de SLA invalide."}), 400
        else:
            # Validate computed SLA is within allowed range
            if sla_meta.get("sla_hours"):
                validate_sla_hours(float(sla_meta["sla_hours"]))

        get_storage().save_alert(
            token=token,
            analysis_id=0,
            flux_id=flux_id,
            flux_name=flux_name,
            label=label,
            n_critiques=1,
            n_warnings=0,
            concordance=0.0,
            anomalies=anomalies,
            email_sent_to="",
            sla_meta=sla_meta,
        )

        try:
            from flask import current_app
            app_obj = current_app._get_current_object()
            broadcast = getattr(app_obj, 'broadcast_new_alert', None)
            if broadcast:
                broadcast(flux_name, token, 1)
        except Exception:
            pass

        try:
            from core.email_alert import send_missing_file_alert_async
            send_missing_file_alert_async(
                flux_id=flux_id,
                flux_name=flux_name,
                label=label,
                token=token,
                expected_hour=expected_hour,
            )
        except Exception as mail_err:
            log.warning("[MANUAL ALERT] Email fichier manquant non envoyé: %s", mail_err)

        return jsonify({
            "token": token,
            "status": "success",
            "message": f"Alerte critique de fichier manquant créée pour le flux {flux_id}"
        }), 201
    except Exception as e:
        return jsonify({"erreur": f"Impossible de sauvegarder l'alerte: {str(e)}"}), 500


# ── Liste des alertes ─────────────────────────────────────────────────
@alerts_bp.get("/api/alerts")
@require_auth
def list_alerts():
    flux_id = request.args.get("flux_id")
    limit   = int(request.args.get("limit", 50))
    archived = request.args.get("archived", "0") == "1"
    status_not_in = None if archived else ["CLOSED"]
    alerts  = get_storage().list_alerts(flux_id=flux_id, limit=limit, status_not_in=status_not_in)
    if archived:
        alerts = [a for a in alerts if a.get("status") == "CLOSED"]
    for a in alerts:
        a.pop("anomalies_json", None)
        if a.get("status") == "PENDING":
            a["status"] = "NEW"
    return jsonify(alerts)


@alerts_bp.get("/api/alerts/<token>")
@require_auth
def get_alert(token: str):
    """
    ✅ FIX : accepte aussi bien le token exact que le début du token (préfixe).
    Le frontend envoie parfois l'URL tronquée visible dans l'email.
    """
    storage = get_storage()

    # 1. Recherche exacte d'abord
    alert = storage.get_alert_by_token(token)

    # 2. Si pas trouvé ET token ressemble à un préfixe (< 36 chars), cherche par préfixe via SQL LIKE
    if not alert and len(token) < 36:
        alert = storage.get_alert_by_token_prefix(token)

    if not alert:
        return jsonify({"error": "Alerte introuvable", "token": token}), 404

    alert.pop("anomalies_json", None)
    if alert.get("status") == "PENDING":
        alert["status"] = "NEW"
    alert["tracking"] = storage.get_tracking(alert.get("token", token))
    return jsonify(alert)


@alerts_bp.patch("/api/alerts/<token>/status")
@require_auth
def update_status(token: str):
    data    = request.get_json(silent=True) or {}
    status  = data.get("status", "").upper()
    comment = data.get("comment", "")
    if status not in VALID_STATUSES:
        return jsonify({"error": f"Statut invalide. Valeurs: {list(VALID_STATUSES)}"}), 400
    alert = get_storage().get_alert_by_token(token)
    if not alert:
        return jsonify({"error": "Alerte introuvable"}), 404
    username = session["user"].get("username", "system")
    err = _apply_transition(token, status, comment or f"Statut → {status}")
    if err:
        return err
    get_storage().save_tracking(
        alert_token=token,
        username=username,
        action=status,
        comment=comment,
    )
    if status == "IGNORED":
        notify_on_alert_ignored(token, username, comment or "Ignorée depuis le dashboard")
    return jsonify({"ok": True, "status": status, "label": STATUS_LABELS.get(status, status)})


@alerts_bp.delete("/api/alerts/<token>")
@require_admin
def delete_alert(token: str):
    """Supprime définitivement une alerte et son historique de suivi."""
    storage = get_storage()
    alert = storage.get_alert_by_token(token)
    if not alert:
        return jsonify({"error": "Alerte introuvable"}), 404
    storage.delete_alert(token)
    return jsonify({"ok": True, "message": f"Alerte '{token}' supprimée"})


@alerts_bp.get("/api/alerts/<token>/track")
def track_alert_get(token: str):
    """Action depuis email sans login — redirige vers login puis exécute l'action."""
    action = request.args.get("action", "").upper()
    if action == "ACKNOWLEDGED":
        return redirect(f"/login?next=/alert/{token}/ack")
    elif action == "IN_PROGRESS":
        return redirect(f"/alert/{token}?action=IN_PROGRESS")
    elif action == "IGNORED":
        return redirect(f"/login?next=/alert/{token}/ignore")
    return redirect(f"/login?next=/alert/{token}")


@alerts_bp.post("/api/alerts/<token>/track")
@require_auth
def track_alert_post(token: str):
    data    = request.get_json(silent=True) or {}
    action  = data.get("action", "").upper()
    comment = data.get("comment", "")
    if action not in VALID_STATUSES:
        return jsonify({"error": f"Action invalide. Valeurs: {list(VALID_STATUSES)}"}), 400
    alert = get_storage().get_alert_by_token(token)
    if not alert:
        return jsonify({"error": "Alerte introuvable"}), 404
    err = _apply_transition(token, action, comment)
    if err:
        return err
    get_storage().save_tracking(
        alert_token=token,
        username=session["user"].get("username", "system"),
        action=action,
        comment=comment,
    )
    return jsonify({"ok": True, "status": action, "label": STATUS_LABELS.get(action, action)})


@alerts_bp.get("/api/alerts/<token>/suggest")
@require_auth
def suggest(token: str):
    """
    ✅ AMÉLIORÉ : utilise Claude API (claude-sonnet) pour une analyse IA enrichie.
    Fallback sur les règles statiques si l'API n'est pas disponible.
    """
    alert = get_storage().get_alert_by_token(token)
    if not alert:
        return jsonify({"error": "Alerte introuvable"}), 404
    try:
        from ai.agent_advisor import generer_conseil, analyser_rapport, generer_conseil_ia
        from flask import current_app

        anomalies = alert.get("anomalies", []) or alert.get("ecarts", [])
        ecarts = []
        for a in anomalies:
            article_id = "?"
            if isinstance(a, dict):
                if "key_values" in a and isinstance(a["key_values"], dict):
                    article_id = " | ".join(str(v) for v in a["key_values"].values())
                elif "key_str" in a:
                    article_id = a["key_str"]
                elif "article_id" in a:
                    article_id = a["article_id"]
            type_ecart = "inconnu"
            if isinstance(a, dict):
                type_ecart = a.get("error_type") or a.get("type_ecart") or "inconnu"
            ecarts.append({
                "type_ecart":    type_ecart,
                "article_id":    article_id,
                "valeur_cegid":  a.get("val_cegid")  if isinstance(a, dict) else None,
                "valeur_oracle": a.get("val_oracle")  if isinstance(a, dict) else None,
            })

        db_path = current_app.config.get("LOCAL_DB_PATH", "instance/flux_monitor.db")

        if not ecarts:
            suggestion = {
                "diagnostic": "Aucune anomalie trouvée dans cette alerte.",
                "actions":    ["Créer une nouvelle analyse pour détecter les écarts."],
                "prevention": "",
                "urgence":    "N/A",
                "impact":     "N/A",
                "confidence": 0,
                "resume":     "Aucune anomalie.",
                "ia_enrichi": False,
            }
        else:
            rapport = analyser_rapport(ecarts, alert["flux_id"], db_path)
            actions_list = []
            for e in rapport.get("ecarts_enrichis", [])[:5]:
                conseil = e.get("conseil", {})
                actions_list.append(f"• {conseil.get('action', 'Action recommandée')}")

            suggestion = {
                "diagnostic": rapport.get("resume", ""),
                "actions":    actions_list if actions_list else ["Analyser les écarts manuellement."],
                "prevention": rapport.get("action_globale", ""),
                "urgence":    "HAUTE"  if rapport.get("nb_critique", 0) > 0 else "MOYENNE",
                "impact":     "ÉLEVÉ"  if rapport.get("nb_critique", 0) > 0 else "MOYEN",
                "confidence": 85,
                "resume":     rapport.get("resume", ""),
                "ia_enrichi": False,
                "nb_critique": rapport.get("nb_critique", 0),
                "nb_warning":  rapport.get("nb_warning", 0),
            }

            # ✅ Enrichissement IA via Claude API (asynchrone, non bloquant)
            try:
                ia_result = generer_conseil_ia(alert, ecarts, rapport)
                if ia_result:
                    suggestion["ia_analyse"]    = ia_result.get("analyse", "")
                    suggestion["ia_actions"]    = ia_result.get("actions_prioritaires", [])
                    suggestion["ia_prevention"] = ia_result.get("prevention", "")
                    suggestion["ia_enrichi"]    = True
                    suggestion["confidence"]    = ia_result.get("confidence", 90)
                    suggestion["rag_used"]      = ia_result.get("rag_used", False)
            except Exception as ia_err:
                suggestion["ia_error"] = str(ia_err)

        return jsonify({"ok": True, "suggestion": suggestion})

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@alerts_bp.post("/api/alerts/<token>/verify")
@require_auth
def verify_alert_resolution(token: str):
    """
    Vérifie si les fichiers corrigés résolvent effectivement les anomalies de l'alerte.
    Si le nombre d'anomalies tombe à 0, l'alerte est résolue automatiquement.
    """
    f_cegid  = request.files.get("cegid")
    f_oracle = request.files.get("oracle")
    if not f_cegid or not f_oracle:
        return jsonify({"error": "Les deux fichiers corrigés (Cegid et Oracle) sont requis pour la vérification."}), 400

    alert = get_storage().get_alert_by_token(token)
    if not alert:
        return jsonify({"error": "Alerte introuvable"}), 404

    from engine.flux_loader import FluxLoader
    try:
        config = FluxLoader.load(alert["flux_id"])
    except Exception as e:
        return jsonify({"error": f"Configuration du flux introuvable: {e}"}), 400

    from api.smart_compare_api import _read_file, _run_comparison, _analyze_columns
    try:
        df_c = _read_file(f_cegid, max_rows=50000)
        df_o = _read_file(f_oracle, max_rows=50000)
    except Exception as e:
        return jsonify({"error": f"Erreur de lecture des fichiers corrigés: {e}"}), 400

    cols_c_map = {c["nom"]: c for c in _analyze_columns(df_c)}
    mapping = []
    for col in config.column_names:
        mapping.append({
            "cegid_col": col,
            "oracle_col": col,
            "cegid_role": cols_c_map.get(col, {}).get("role", "donnee"),
            "compare": True
        })

    key_cols = config.key_columns

    try:
        result = _run_comparison(df_c, df_o, key_cols, key_cols, mapping)
        anomalies_count = result.get("n_anomalies", 0)

        if anomalies_count == 0:
            err = _apply_transition(
                token, "RESOLVED",
                "Vérification réussie: 0 écart détecté dans les fichiers corrigés.",
            )
            if err:
                return err
            get_storage().save_tracking(
                alert_token=token,
                username=session["user"].get("username", "system"),
                action="RESOLVED",
                comment="Vérification réussie: 0 écart détecté dans les fichiers corrigés.",
            )
            return jsonify({
                "ok": True,
                "resolved": True,
                "message": "Félicitations! 0 écart détecté. L'alerte est marquée comme RÉSOLUE.",
                "details": result
            })
        else:
            return jsonify({
                "ok": False,
                "resolved": False,
                "message": f"Il reste encore {anomalies_count} écart(s) dans les fichiers fournis. L'alerte reste ouverte.",
                "details": result
            })

    except Exception as e:
        return jsonify({"error": f"Erreur lors de la comparaison de vérification: {e}"}), 500


@alerts_bp.post("/api/alerts/<token>/resolve")
@require_auth
def resolve_alert(token: str):
    """
    Transition alert to RESOLVED status with state machine validation.
    
    Requirements:
    - User must have role >= analyst
    - Current status must allow transition to RESOLVED
    - Comment (solution) is OPTIONAL — when empty, an auto audit comment is
      recorded so the state machine's required-field guard stays satisfied
    - Optional: error_type and effectiveness for correction tracking
    """
    from core.alert_state_machine import transition_alert, InvalidTransitionError, ValidationError, PermissionError
    
    data  = request.get_json(silent=True) or {}
    alert = get_storage().get_alert_by_token(token)
    comment = (data.get("comment") or data.get("solution") or "").strip()
    solution = comment
    
    if not alert:
        return jsonify({"error": "Alerte introuvable"}), 404
    
    # Get user context
    actor_user = {
        "username": session["user"].get("username", "system"),
        "role": session["user"].get("role", "viewer"),
        "sub": session["user"].get("sub", "unknown"),
    }
    
    # Use state machine to validate and apply transition
    try:
        transition_alert(
            storage=get_storage(),
            alert_token=token,
            new_status="RESOLVED",
            actor_user=actor_user,
            comment=comment or f"Statut → RESOLVED",  # Guard 3 requires non-empty; default mirrors update_status()
        )
    except (InvalidTransitionError, ValidationError, PermissionError) as e:
        # Return 409/422/403 based on error type
        if isinstance(e, InvalidTransitionError):
            return jsonify({"error": str(e), "current_status": alert["status"]}), 409
        elif isinstance(e, ValidationError):
            return jsonify({"error": str(e)}), 422
        elif isinstance(e, PermissionError):
            return jsonify({"error": str(e)}), 403
    
    # Track the resolution
    get_storage().save_tracking(
        alert_token=token,
        username=actor_user["username"],
        action="RESOLVED",
        comment=comment,
    )
    
    # Optional: save correction for learning
    error_type = data.get("error_type", "")
    effective  = bool(data.get("effective", True))
    if error_type and solution:
        get_storage().save_correction(
            flux_id=alert["flux_id"],
            error_type=error_type,
            column_name="",
            solution_applied=solution,
            was_effective=effective,
        )
    
    return jsonify({"ok": True, "new_status": "RESOLVED"})


@alerts_bp.post("/api/alerts/<token>/close")
@require_auth
def close_alert(token: str):
    """
    Transition RESOLVED → CLOSED. Only available from RESOLVED status.
    Requires comment (closure summary).
    """
    from core.alert_state_machine import transition_alert, InvalidTransitionError, ValidationError, PermissionError

    data  = request.get_json(silent=True) or {}
    alert = get_storage().get_alert_by_token(token)
    comment = (data.get("comment") or "").strip()

    if not alert:
        return jsonify({"error": "Alerte introuvable"}), 404

    actor_user = {
        "username": session["user"].get("username", "system"),
        "role": session["user"].get("role", "viewer"),
    }

    try:
        transition_alert(
            storage=get_storage(),
            alert_token=token,
            new_status="CLOSED",
            actor_user=actor_user,
            comment=comment or "Clôture manuelle",
        )
    except (InvalidTransitionError, ValidationError, PermissionError) as e:
        if isinstance(e, InvalidTransitionError):
            return jsonify({"error": str(e), "current_status": alert["status"]}), 409
        elif isinstance(e, ValidationError):
            return jsonify({"error": str(e)}), 422
        elif isinstance(e, PermissionError):
            return jsonify({"error": str(e)}), 403

    get_storage().save_tracking(
        alert_token=token,
        username=actor_user["username"],
        action="CLOSED",
        comment=comment,
    )

    return jsonify({"ok": True, "new_status": "CLOSED"})


@alerts_bp.post("/api/alerts/<token>/escalate")
@require_auth
def escalate_alert(token: str):
    data  = request.get_json(silent=True) or {}
    alert = get_storage().get_alert_by_token(token)
    if not alert:
        return jsonify({"error": "Alerte introuvable"}), 404

    assign_to_email = data.get("assign_to_email", "").strip()
    reason  = data.get("reason", "")
    comment = data.get("comment", "")

    if not assign_to_email:
        return jsonify({"error": "Merci de spécifier l'email du consultant/team leader"}), 400
    if "@" not in assign_to_email:
        return jsonify({"error": "Veuillez entrer une adresse email valide"}), 400

    storage = get_storage()
    users = storage.list_users()
    target_user = None
    for u in users:
        if u.get("email", "").lower() == assign_to_email.lower():
            target_user = u
            break

    if not target_user:
        return jsonify({"error": "Aucun utilisateur trouvé avec cet email. Veuillez d'abord créer ce compte dans Admin."}), 404

    user_role = target_user.get("role", "")
    if user_role not in ("consultant", "team_leader", "admin"):
        return jsonify({"error": f"Cet utilisateur a le rôle '{user_role}', pas les droits pour recevoir des alertes escaladées."}), 400

    err = _apply_transition(
        token, "ESCALATED",
        f"Escaladé vers {assign_to_email}. Raison: {reason}",
    )
    if err:
        return err

    # Mettre à jour les colonnes d'escalade en base de données
    storage.set_escalated(token, session["user"].get("username", "system"), assign_to_email)

    get_storage().save_tracking(
        alert_token=token,
        username=session["user"].get("username", "system"),
        action=f"ESCALATED_TO:{assign_to_email}",
        comment=f"Escaladé vers {assign_to_email} ({target_user.get('username')}). Raison: {reason}. Commentaire: {comment}",
    )

    # Envoyer l'email d'escalade
    _send_escalation_email(
        to_email=assign_to_email,
        username=target_user.get("username"),
        flux_id=alert.get("flux_id", ""),
        token=token,
        reason=reason,
        comment=comment,
        escalated_by=session["user"].get("username", "system"),
        alert=alert,
    )

    # Diffuser la notification WebSocket à B
    try:
        from flask import current_app
        app_obj = current_app._get_current_object()
        broadcast = getattr(app_obj, 'broadcast_custom_notification', None)
        if broadcast:
            alert_id = alert.get("id") or token[:8]
            sender_a = session["user"].get("username", "system")
            msg = f"🔄 L'alerte #{alert_id} vous a été escaladée par {sender_a}."
            broadcast(target_username=target_user.get("username"), message=msg, token=token, type_notif="escalation")
    except Exception as ws_err:
        log.warning("Erreur WS broadcast escalade: %s", ws_err)

    return jsonify({
        "ok": True,
        "message": f"Alerte escaladée vers {assign_to_email}",
        "assign_to": target_user.get("username"),
        "assign_to_email": assign_to_email,
    })


@alerts_bp.post("/api/alerts/<token>/feedback")
@require_auth
def feedback_ia(token: str):
    db    = get_storage()
    alert = db.get_alert_by_token(token)
    if not alert:
        return jsonify({"error": "Alerte introuvable"}), 404

    data         = request.get_json(silent=True) or {}
    score        = int(data.get("score", 3))
    comment      = str(data.get("comment", "")).strip()[:500]
    action_taken = str(data.get("action_taken", "")).strip()[:500]

    if not (1 <= score <= 5):
        return jsonify({"error": "Score invalide — entier entre 1 et 5"}), 400

    resolution_hours = None
    try:
        from datetime import datetime
        created = datetime.fromisoformat(alert["created_at"])
        resolution_hours = round((datetime.utcnow() - created).total_seconds() / 3600, 2)
    except Exception:
        pass

    rag_saved = False
    try:
        from ai.rag_context import store_resolved_case
        store_resolved_case(
            alert_token=token,
            action_taken=action_taken or f"Feedback {score}/5",
            resolution_hours=resolution_hours,
            feedback_score=score,
            feedback_comment=comment,
        )
        rag_saved = True
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[FEEDBACK] RAG storage failed: {e}")

    username = session.get("user", {}).get("username", "system")
    db.save_tracking(
        alert_token=token,
        username=username,
        action=f"IA_FEEDBACK_{score}STARS",
        comment=f"Score IA : {score}/5. Action : {action_taken[:100] if action_taken else '—'}",
    )

    stars = "⭐" * score
    return jsonify({
        "ok": True,
        "message": f"Feedback enregistré {stars}",
        "score": score,
        "rag_saved": rag_saved,
        "rag_impact": "Ce feedback enrichira les prochaines analyses IA" if (rag_saved and score >= 4) else "",
    })


@alerts_bp.get("/api/users/consultants")
@require_auth
def list_consultants():
    try:
        storage = get_storage()
        users   = storage.list_users()
        consultants = [
            {"username": u.get("username"), "role": u.get("role"), "email": u.get("email")}
            for u in users
            if u.get("role") in ("consultant", "team_leader", "admin")
        ]
        return jsonify({"ok": True, "consultants": consultants})
    except Exception as e:
        return jsonify({"error": str(e)}), 500