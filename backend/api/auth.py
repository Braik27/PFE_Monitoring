import functools
import logging
import os
import smtplib
import threading
import uuid
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote, urlencode
from flask import Blueprint, jsonify, request, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from config import settings
from storage import get_storage

log = logging.getLogger("auth")


def require_auth(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        user = session.get("user", {})
        if not user:
            return jsonify({"error": "Authentication required"}), 401
        if user.get("role") != "admin":
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated


auth_bp = Blueprint("auth", __name__)


@auth_bp.post("/api/login")
def login():
    import logging
    from urllib.parse import urlparse
    
    log = logging.getLogger("auth")
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    next_url = data.get("next", "/")
    log.info(f"LOGIN next_url: {next_url}")
    
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    user = get_storage().get_user(username)
    if not user:
        if "@" in username:
            user = get_storage().get_user_by_email(username)
        if not user:
            return jsonify({"error": "Invalid credentials"}), 401

    from werkzeug.security import check_password_hash
    if not check_password_hash(user.get("password_hash", ""), password):
        return jsonify({"error": "Invalid credentials"}), 401

    if not user.get("active", 1):
        return jsonify({"error": "Account disabled"}), 403

    session["user"] = {
        "id": user.get("id"),
        "username": user.get("username"),
        "role": user.get("role", "consultant"),
        "full_name": user.get("full_name", ""),
        "email": user.get("email", ""),
    }

    # Valider et nettoyer next_url pour sécurité
    parsed = urlparse(next_url)
    if parsed.netloc or not next_url.startswith("/"):
        next_url = "/"
    log.info(f"LOGIN redirect_to: {next_url}")

    return jsonify({"ok": True, "user": {
        "id": user.get("id"),
        "username": user.get("username"),
        "role": user.get("role", "consultant"),
        "full_name": user.get("full_name", ""),
        "email": user.get("email", ""),
        "avatar": user.get("avatar"),
    }, "next": next_url})


@auth_bp.post("/api/logout")
def logout():
    session.pop("user", None)
    return jsonify({"ok": True})


@auth_bp.get("/api/me")
@require_auth
def me():
    return jsonify(_current_user_payload())


@auth_bp.get("/api/session")
def session_info():
    if not session.get("user"):
        return jsonify({"user": None})
    return jsonify({"user": _current_user_payload()})


def _current_user_payload():
    user = dict(session.get("user", {}))
    user_id = user.get("id")
    if user_id:
        db_user = get_storage().get_user_by_id(user_id)
        if db_user:
            user["full_name"] = db_user.get("full_name", "")
            user["email"]     = db_user.get("email", "")
            user["avatar"]    = db_user.get("avatar")
    # Nombre d analyses de cet utilisateur
    try:
        username = user.get("username", "")
        user["n_analyses"] = get_storage().count_analyses_by_analyst(username)
    except Exception:
        user["n_analyses"] = 0
    return user


@auth_bp.get("/api/auth/config")
def auth_config():
    return jsonify({
        "google_enabled": bool(os.environ.get("GOOGLE_CLIENT_ID")),
        "mock_mode": settings.allow_google_mock,
    })


@auth_bp.post("/api/auth/forgot-password")
def forgot_password():
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip()
    log.info("[FORGOT-PASSWORD] Request received for email: %s", email)
    if not email:
        return jsonify({"error": "Email requis"}), 400

    user = get_storage().get_user_by_email(email)
    log.info("[FORGOT-PASSWORD] User lookup result: %s", "found" if user else "not found")
    if user:
        token = uuid.uuid4().hex
        expires_at = (datetime.utcnow() + timedelta(hours=1)).isoformat()
        get_storage().update_reset_token(user["id"], token, expires_at)

        if os.environ.get("ALERT_EMAIL_ENABLED", "false").lower() == "true":
            base_url = os.environ.get("APP_BASE_URL", "")
            reset_link = f"{base_url}/reset-password?token={token}"
            subject = "Réinitialisation de mot de passe"
            body = f"""<html><body style='font-family:Arial,sans-serif;background:#f8fafc;padding:20px'>
<div style='max-width:600px;margin:0 auto;background:#fff;border-radius:10px;padding:30px;border:1px solid #dde3f0'>
  <h2 style='color:#1e40af;margin-bottom:8px'>🔐 Réinitialisation de mot de passe</h2>
  <p style='color:#64748b;font-size:13px'>Bonjour <strong>{user.get('full_name', '') or user.get('username', '')}</strong>,</p>
  <p>Cliquez sur le lien ci-dessous pour réinitialiser votre mot de passe :</p>
  <p><a href='{reset_link}' style='color:#1d4ed8;font-weight:700'>{reset_link}</a></p>
  <p style='color:#dc2626;font-size:12px'>Ce lien expire dans 1 heure.</p>
  <div style='margin-top:24px;padding:14px;background:#f1f5f9;border-radius:8px;font-size:12px;color:#64748b'>
    <strong>Flux Monitor — TimSoft</strong>
  </div>
</div></body></html>"""

            smtp_host = os.environ.get("ALERT_SMTP_HOST", "")
            smtp_port = int(os.environ.get("ALERT_SMTP_PORT", "587"))
            smtp_user = os.environ.get("ALERT_SMTP_USER", "")
            smtp_pass = os.environ.get("ALERT_SMTP_PASSWORD", "")
            from_addr = os.environ.get("ALERT_EMAIL_FROM", smtp_user)

            if smtp_host and smtp_user:
                def _do_send():
                    try:
                        msg = MIMEMultipart("alternative")
                        msg["Subject"] = subject
                        msg["From"]    = from_addr
                        msg["To"]      = email
                        msg.attach(MIMEText(body, "html", "utf-8"))
                        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as s:
                            s.ehlo()
                            s.starttls()
                            s.login(smtp_user, smtp_pass)
                            s.sendmail(from_addr, [email], msg.as_string())
                        log.info("[FORGOT-PASSWORD] Email envoye -> %s", email)
                    except Exception as e:
                        log.error("[FORGOT-PASSWORD] Erreur SMTP: %s", e)
                        log.warning("[FORGOT-PASSWORD] Fallback dev: lien de reset -> %s", reset_link)

                threading.Thread(target=_do_send, daemon=True).start()
            else:
                log.warning("[FORGOT-PASSWORD] SMTP non configure — fallback dev: lien de reset -> %s", reset_link)

    return jsonify({"ok": True})


@auth_bp.post("/api/auth/verify-reset-token")
def verify_reset_token():
    data = request.get_json(silent=True) or {}
    token = data.get("token", "").strip()
    if not token:
        return jsonify({"valid": False, "email": ""}), 400

    user = get_storage().get_user_by_reset_token(token)
    if not user:
        return jsonify({"valid": False, "email": ""}), 200

    expires_at = user.get("reset_token_expires_at", "")
    valid = False
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at)
            valid = datetime.utcnow() < exp_dt
        except Exception:
            valid = False

    return jsonify({"valid": valid, "email": user.get("email", "")})


@auth_bp.post("/api/auth/reset-password")
def reset_password():
    data = request.get_json(silent=True) or {}
    token = data.get("token", "").strip()
    password = data.get("password", "")

    if not token or not password:
        return jsonify({"error": "Token et mot de passe requis"}), 400
    if len(password) < 6:
        return jsonify({"error": "Le mot de passe doit contenir au moins 6 caractères"}), 400

    user = get_storage().get_user_by_reset_token(token)
    if not user:
        return jsonify({"error": "Token invalide"}), 400

    expires_at = user.get("reset_token_expires_at", "")
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at)
            if datetime.utcnow() >= exp_dt:
                return jsonify({"error": "Token expiré"}), 400
        except Exception:
            return jsonify({"error": "Token invalide"}), 400

    get_storage().update_user_password(user["id"], generate_password_hash(password))
    get_storage().update_reset_token(user["id"], None, None)
    return jsonify({"ok": True})


@auth_bp.get("/api/auth/google/login")
def google_login():
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    if google_client_id:
        redirect_uri = url_for("auth.google_callback", _external=True)
        auth_url = (
            "https://accounts.google.com/o/oauth2/v2/auth?"
            f"client_id={google_client_id}&redirect_uri={quote(redirect_uri)}"
            "&response_type=code&scope=openid%20email%20profile"
        )
        return redirect(auth_url)

    if settings.allow_google_mock:
        return redirect(url_for("auth.google_callback", mock="1", email="google-mock@timsfort.com", name="Google Mock"))

    return jsonify({"error": "Google OAuth non configure"}), 503


@auth_bp.get("/api/auth/google/callback")
def google_callback():
    mock = request.args.get("mock")
    if mock == "1":
        # Backdoor désactivée par défaut : uniquement en développement ET si
        # ALLOW_GOOGLE_MOCK=true (voir settings.allow_google_mock).
        if not settings.allow_google_mock:
            return jsonify({"error": "OAuth mock désactivé"}), 403
        email = request.args.get("email", "google-mock@timsfort.com")
        name = request.args.get("name", "Google Mock")
        user = get_storage().get_user_by_email(email)
        if not user:
            user_id = get_storage().save_user(email.split("@")[0], generate_password_hash(uuid.uuid4().hex), "consultant")
            get_storage().update_user_profile(user_id, full_name=name, email=email)
            user = get_storage().get_user_by_email(email)

        if user:
            session["user"] = {
                "id": user.get("id"),
                "username": user.get("username"),
                "role": user.get("role", "consultant"),
                "full_name": user.get("full_name", ""),
                "email": user.get("email", ""),
            }
        return redirect("/")

    code = request.args.get("code")
    if not code:
        return redirect("/?error=no_code")

    google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    redirect_uri = url_for("auth.google_callback", _external=True)

    import urllib.request
    import json as _json
    token_data = {}
    try:
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=urlencode({
                "code": code,
                "client_id": google_client_id,
                "client_secret": google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            token_data = _json.loads(resp.read())
    except Exception as e:
        return redirect(f"/?error=token_exchange_failed:{e}")

    access_token = token_data.get("access_token")
    if not access_token:
        return redirect("/?error=no_access_token")

    try:
        req = urllib.request.Request(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            profile = _json.loads(resp.read())
    except Exception as e:
        return redirect(f"/?error=profile_fetch_failed:{e}")

    email = profile.get("email", "")
    name = profile.get("name", "")
    if not email:
        return redirect("/?error=no_email")

    user = get_storage().get_user_by_email(email)
    if not user:
        user_id = get_storage().save_user(email.split("@")[0], generate_password_hash(uuid.uuid4().hex), "consultant")
        get_storage().update_user_profile(user_id, full_name=name, email=email)
        user = get_storage().get_user_by_email(email)

    if user:
        session["user"] = {
            "id": user.get("id"),
            "username": user.get("username"),
            "role": user.get("role", "consultant"),
            "full_name": user.get("full_name", ""),
            "email": user.get("email", ""),
        }

    return redirect("/")


def _precompute_stats():
    analyses = get_storage().list_analyses(limit=10000)
    alerts   = get_storage().list_alerts(limit=10000)
    analyses_by_analyst = {}
    for a in analyses:
        analyst = (a.get("summary") or {}).get("analyst", "")
        if analyst:
            analyses_by_analyst[analyst] = analyses_by_analyst.get(analyst, 0) + 1
    alert_status_by_token = {}
    for al in alerts:
        alert_status_by_token[al.get("token", "")] = al.get("status", "")
    return analyses_by_analyst, alert_status_by_token


admin_bp = Blueprint("admin", __name__)


@admin_bp.get("/api/admin/users")
@require_admin
def list_users_admin():
    """Liste tous les utilisateurs avec leurs stats."""
    users = get_storage().list_users()
    analyses_by_analyst, alert_status_by_token = _precompute_stats()
    for user in users:
        username = user.get("username", "")
        user["n_analyses"] = analyses_by_analyst.get(username, 0)
        user["n_pending_alerts"] = sum(1 for s in alert_status_by_token.values() if s in ("PENDING", "ACKNOWLEDGED"))
        user["n_resolved"] = sum(1 for s in alert_status_by_token.values() if s == "RESOLVED")
    return jsonify(users)


@admin_bp.post("/api/admin/users")
@require_admin
def create_user():
    data      = request.get_json(silent=True) or {}
    username  = data.get("username", "").strip()
    password  = data.get("password", "")
    role      = data.get("role", "consultant")
    email     = data.get("email", "").strip()
    full_name = data.get("full_name", "").strip()

    if not username or not password:
        return jsonify({"error": "Nom d'utilisateur et mot de passe requis"}), 400
    if len(password) < 6:
        return jsonify({"error": "Le mot de passe doit contenir au moins 6 caractères"}), 400
    if get_storage().get_user(username):
        return jsonify({"error": "Ce nom d'utilisateur existe déjà"}), 400

    user_id = get_storage().save_user(username, generate_password_hash(password), role)
    get_storage().update_user_profile(user_id, full_name=full_name, email=email)
    return jsonify({"ok": True, "id": user_id}), 201


@admin_bp.put("/api/admin/users/<int:user_id>")
@require_admin
def update_user(user_id: int):
    data = request.get_json(silent=True) or {}
    updates = {}
    if "role"      in data: updates["role"]      = data["role"]
    if "full_name" in data: updates["full_name"]  = data["full_name"]
    if "email"     in data: updates["email"]      = data["email"]
    if "active"    in data: updates["active"]     = 1 if data["active"] else 0
    if updates:
        get_storage().update_user(user_id, **updates)
    return jsonify({"ok": True})


@admin_bp.put("/api/admin/users/<int:user_id>/password")
@require_admin
def reset_password(user_id: int):
    data         = request.get_json(silent=True) or {}
    new_password = data.get("password", "")
    if not new_password or len(new_password) < 6:
        return jsonify({"error": "Le mot de passe doit contenir au moins 6 caractères"}), 400
    get_storage().update_user_password(user_id, generate_password_hash(new_password))
    return jsonify({"ok": True})


@admin_bp.put("/api/admin/users/<int:user_id>/toggle-active")
@require_admin
def toggle_user_active(user_id: int):
    data   = request.get_json(silent=True) or {}
    active = data.get("active", True)
    get_storage().update_user_status(user_id, 1 if active else 0)
    return jsonify({"ok": True})


@admin_bp.delete("/api/admin/users/<int:user_id>")
@require_admin
def delete_user(user_id: int):
    current_user_id = session.get("user", {}).get("id")
    if user_id == current_user_id:
        return jsonify({"error": "Vous ne pouvez pas supprimer votre propre compte"}), 400
    get_storage().delete_user(user_id)
    return jsonify({"ok": True})


@admin_bp.get("/api/admin/stats")
@require_admin
def admin_stats():
    users = get_storage().list_users()
    analyses_by_analyst, alert_status_by_token = _precompute_stats()
    return jsonify({
        "total_analyses": sum(analyses_by_analyst.values()),
        "total_alerts":   len(alert_status_by_token),
        "pending_alerts": sum(1 for s in alert_status_by_token.values() if s in ("NEW", "PENDING", "ACKNOWLEDGED")),
        "total_users":    len(users),
        "active_users":   sum(1 for u in users if u.get("active", 1) == 1),
    })


# ══ DIVISIONS ══════════════════════════════════════════════════════════

@admin_bp.get("/api/divisions")
@require_auth
def list_divisions():
    """Retourne toutes les divisions actives (accessible à tous les utilisateurs connectés)."""
    divs = get_storage().list_divisions()
    return jsonify(divs)


@admin_bp.post("/api/divisions")
@require_admin
def create_division():
    data    = request.get_json(silent=True) or {}
    code    = data.get("code", "").strip().upper()
    name    = data.get("name", "").strip()
    country = data.get("country", "").strip()
    flag    = data.get("flag", "").strip()

    if not code or not name:
        return jsonify({"error": "Code et nom requis"}), 400

    div_id = get_storage().save_division(code, name, country, flag)
    return jsonify({"ok": True, "id": div_id}), 201


@admin_bp.put("/api/divisions/<code>")
@require_admin
def update_division(code: str):
    data = request.get_json(silent=True) or {}
    name    = data.get("name", "").strip()
    country = data.get("country", "").strip()
    flag    = data.get("flag", "").strip()
    if not name:
        return jsonify({"error": "Nom requis"}), 400
    get_storage().save_division(code.upper(), name, country, flag)
    return jsonify({"ok": True})


@admin_bp.delete("/api/divisions/<code>")
@require_admin
def delete_division(code: str):
    get_storage().delete_division(code.upper())
    return jsonify({"ok": True})
