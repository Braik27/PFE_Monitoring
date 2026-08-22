import logging, os, time as _time, pathlib, json
from dotenv import load_dotenv
from flask import Flask, send_from_directory, g as _g, jsonify, session, request
from werkzeug.security import generate_password_hash
from core.job_manager import get_job_manager


load_dotenv()

# ── Fail-closed : refuse de démarrer si la config requise est absente ────
# (SECRET_KEY obligatoire partout ; ADMIN_USER/ADMIN_PASSWORD en production)
from config import settings
settings.validate()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = settings.flask.SECRET_KEY

app.config.update(
    SESSION_COOKIE_HTTPONLY  = True,
    SESSION_COOKIE_SAMESITE  = "Lax",
    SESSION_COOKIE_SECURE    = os.environ.get("FLASK_ENV") == "production",
    SESSION_COOKIE_NAME      = "fm_session",
    PERMANENT_SESSION_LIFETIME = 86400,
)
# ── Détection de l'environnement de déploiement ──────────────────────────
# En production (Azure ou Docker), le frontend React est buildé dans frontend/dist/
# En développement local, Vite sert le frontend via le proxy
FRONTEND_DIST = pathlib.Path(__file__).parent.parent / "frontend" / "dist"
IS_PRODUCTION = os.environ.get("FLASK_ENV") == "production" or FRONTEND_DIST.exists()

# ── WebSocket (flask-sock) ────────────────────────────────────────────
try:
    from flask_sock import Sock
    sock = Sock(app)
    _ws_clients: set = set()

    @sock.route("/ws/alerts")
    def ws_alerts(ws):
        """WebSocket endpoint — pousse les nouvelles alertes vers le frontend.

        Handshake authentifié : la session Flask (cookie fm_session) doit
        contenir un utilisateur ; sinon la connexion est refermée immédiatement.
        """
        log = logging.getLogger("ws")
        user = session.get("user") if session else None
        if not user:
            log.warning("Connexion WS refusée : non authentifiée (%s)", request.remote_addr if request else "?")
            try:
                ws.send(json.dumps({"type": "error", "message": "Authentification requise"}))
                ws.close()
            except Exception:
                pass
            return

        _ws_clients.add(ws)
        log.info("Client WS connecté (%d total)", len(_ws_clients))

        def handle_client_message(msg):
            if msg is None:
                return
            try:
                data = json.loads(msg)
                if data.get("type") == "ping":
                    ws.send(json.dumps({"type": "pong"}))
                    return
            except (json.JSONDecodeError, Exception):
                pass

        try:
            while True:
                msg = ws.receive(timeout=30)
                handle_client_message(msg)
        except Exception:
            pass
        finally:
            _ws_clients.discard(ws)
            try:
                ws.close()
            except Exception:
                pass
            log.info("Client WS déconnecté (%d restants)", len(_ws_clients))

    def broadcast_new_alert(flux_name: str, token: str, n_critiques: int = 0):
        """Diffuse une nouvelle alerte à tous les clients WS connectés."""
        import json
        payload = json.dumps({
            "type":        "new_alert",
            "flux_name":   flux_name,
            "token":       token,
            "n_critiques": n_critiques,
        })
        dead = set()
        for client in list(_ws_clients):
            try:
                client.send(payload)
            except Exception:
                dead.add(client)
        _ws_clients -= dead

    def broadcast_job_progress(job):
        """Diffuse la progression des jobs asynchrones à tous les clients WS."""
        import json
        payload = json.dumps({
            "type":      "job_progress",
            "job_id":    job.job_id,
            "status":    job.status,
            "progress":  job.progress,
            "step_label": job.step_label,
            "error":     job.error,
            "meta":      job.meta,
        })
        dead = set()
        for client in list(_ws_clients):
            try:
                client.send(payload)
            except Exception:
                dead.add(client)
        _ws_clients -= dead

    def broadcast_custom_notification(target_username: str, message: str, token: str, type_notif: str):
        """Diffuse une notification personnalisée ciblée."""
        import json
        payload = json.dumps({
            "type":            "custom_notification",
            "target_username": target_username,
            "message":         message,
            "token":           token,
            "type_notif":      type_notif,
        })
        dead = set()
        for client in list(_ws_clients):
            try:
                client.send(payload)
            except Exception:
                dead.add(client)
        _ws_clients -= dead

    app.broadcast_new_alert = broadcast_new_alert
    app.broadcast_job_progress = broadcast_job_progress
    app.broadcast_custom_notification = broadcast_custom_notification
    get_job_manager().set_broadcaster(broadcast_job_progress)

except ImportError:
    logging.getLogger(__name__).warning(
        "flask-sock non installé — WebSocket désactivé. "
        "Installez-le via: pip install flask-sock"
    )
    app.broadcast_new_alert = lambda *a, **kw: None
    app.broadcast_job_progress = lambda *a, **kw: None
    app.broadcast_custom_notification = lambda *a, **kw: None

# ── Gestion globale des erreurs ────────────────────────────────────
from werkzeug.exceptions import HTTPException
import traceback

@app.errorhandler(Exception)
def handle_exception(e):
    """Gestionnaire global d'exceptions - retourne JSON en cas d'erreur API."""
    from flask import jsonify as _jsonify
    if isinstance(e, HTTPException):
        return _jsonify({"error": e.description}), e.code
    
    logging.getLogger(__name__).exception("Erreur 500 non gérée: %s", str(e))
    return _jsonify({"error": "Erreur serveur interne", "details": str(e)}), 500

# ── Blueprints ────────────────────────────────────────────────────────
from api.auth         import auth_bp
from api.flux_api     import flux_bp
from api.analysis     import analysis_bp
from api.alerts_api   import alerts_bp
from api.sla_api      import sla_bp
from api.daily_report import report_bp
from api.auth         import admin_bp
from api.smart_compare_api import smart_bp
from api.smart_compare_async import smart_async_bp
from api.system_status import system_bp
from api.assistant_api import assistant_bp

from api.customerbalance_report import cb_bp

for bp in (auth_bp, flux_bp, analysis_bp, admin_bp, alerts_bp, sla_bp, report_bp, smart_bp, smart_async_bp, system_bp, assistant_bp, cb_bp):
    app.register_blueprint(bp)



from core.monitoring import record_request, setup_azure_monitoring, get_metrics_summary

@app.before_request
def _start_timer():
    _g.req_start = _time.time()

@app.after_request
def _record_time(response):
    if hasattr(_g, "req_start"):
        duration_ms = (_time.time() - _g.req_start) * 1000
        is_error = response.status_code >= 500
        record_request(duration_ms, is_error=is_error)
    return response


# ── Endpoint métriques performance Flask ─────────────────────────────
@app.get("/api/system/perf")
def perf_metrics():
    from flask import jsonify
    return jsonify(get_metrics_summary())


# ── Static routes ─────────────────────────────────────────────────────
@app.get("/")
def index():
    if IS_PRODUCTION and FRONTEND_DIST.exists():
        response = send_from_directory(FRONTEND_DIST, "index.html")
    else:
        response = send_from_directory("templates", "index.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"]        = "no-cache"
    response.headers["Expires"]       = "0"
    return response


@app.get("/login")
def login_page():
    if IS_PRODUCTION and FRONTEND_DIST.exists():
        response = send_from_directory(FRONTEND_DIST, "index.html")
    else:
        response = send_from_directory("templates", "login.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


# ── SPA fallback : sert index.html pour toute route non-API
#    (nécessaire pour que React Router fonctionne en production)
@app.get("/<path:path>")
def spa_fallback(path: str):
    """Sert les fichiers du build Vite (assets, favicon, …) ou index.html pour le SPA."""
    from flask import abort

    if path.startswith(("api/", "ws/", "static/")):
        abort(404)

    if IS_PRODUCTION and FRONTEND_DIST.exists():
        root = FRONTEND_DIST.resolve()
        try:
            full = (FRONTEND_DIST / path).resolve()
            full.relative_to(root)
        except (ValueError, OSError):
            full = None
        if full is not None and full.is_file():
            rel = full.relative_to(root).as_posix()
            return send_from_directory(FRONTEND_DIST, rel)
        response = send_from_directory(FRONTEND_DIST, "index.html")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response
    return send_from_directory("templates", "index.html")


@app.route("/static/<path:filename>")
def serve_static(filename):
    response = send_from_directory("static", filename)
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


# ── Routes d'action directes depuis email ────────────────────────────
@app.get("/alert/<token>/ack")
def alert_ack_direct(token: str):
    """
    ✅ FIX : Route appelée depuis l'email "Je prends en charge".
    - Si non connecté  → redirige vers /login?next=/alert/<token>/ack
    - Si connecté      → exécute l'action et redirige vers /alerts?token={token}
    """
    from flask import session, redirect
    user = session.get("user")
    if not user:
        return redirect(f"/login?next=/alert/{token}/ack")
    from api.alerts_api import execute_alert_action
    return execute_alert_action(token, "ACKNOWLEDGED")


@app.get("/alert/<token>/ignore")
def alert_ignore_direct(token: str):
    """
    ✅ FIX : Route appelée depuis l'email "Ignorer".
    - Si non connecté  → redirige vers /login?next=/alert/<token>/ignore
    - Si connecté      → exécute l'action et redirige vers /alerts?token={token}
    """
    from flask import session, redirect
    user = session.get("user")
    if not user:
        return redirect(f"/login?next=/alert/{token}/ignore")
    from api.alerts_api import execute_alert_action
    return execute_alert_action(token, "IGNORED")


# ── Routes utilitaires ────────────────────────────────────────────────
@app.get("/debug/alerts/<token>")
def debug_alert(token: str):
    from flask import jsonify, session
    from config import settings
    if settings.ENV == "production":
        return jsonify({"error": "Route désactivée en production"}), 403
    user = session.get("user")
    if not user or user.get("role") != "admin":
        return jsonify({"error": "Accès réservé aux administrateurs"}), 403
    from storage import get_storage
    alert = get_storage().get_alert_by_token(token)
    if not alert:
        return jsonify({"error": "Alerte non trouvée"}), 404
    return jsonify(alert)


@app.get("/health")
def health_check():
    from config import settings
    status = {"status": "healthy", "environment": settings.ENV}
    try:
        from storage import get_storage
        get_storage().list_analyses(limit=1)
        status["database"] = "connected"
    except Exception as e:
        status["status"]   = "degraded"
        status["database"] = "error"
        status["error"]    = str(e)
    return status


# ── Bootstrap ─────────────────────────────────────────────────────────
def _bootstrap():
    from storage import get_storage
    db = get_storage()
    db.init_db()
    logging.getLogger(__name__).info("Database initialized/updated")

    # Aucun compte par défaut : les identifiants doivent venir de
    # l'environnement (obligatoire en production — voir settings.validate()).
    admin_user = os.environ.get("ADMIN_USER", "").strip()
    admin_pass = os.environ.get("ADMIN_PASSWORD", "")
    if admin_user and admin_pass:
        if not db.get_user(admin_user):
            db.save_user(admin_user, generate_password_hash(admin_pass), "admin")
            logging.getLogger(__name__).info("Admin créé — %s", admin_user)
    else:
        logging.getLogger(__name__).warning(
            "ADMIN_USER/ADMIN_PASSWORD non définis — aucun compte admin seedé."
        )

    watcher_user = os.environ.get("WATCHER_USER", "").strip()
    watcher_pass = os.environ.get("WATCHER_PASSWORD", "")
    if watcher_user and watcher_pass:
        if not db.get_user(watcher_user):
            db.save_user(watcher_user, generate_password_hash(watcher_pass), "analyst")
            logging.getLogger(__name__).info("Technical user created — %s", watcher_user)
    else:
        logging.getLogger(__name__).info(
            "WATCHER_USER/WATCHER_PASSWORD non définis — compte technique non créé."
        )

    try:
        if not db.list_expected_flux():
            db.save_expected_flux("DOHA", "DOHA", "18:00", "DOHA", 1)
            db.save_expected_flux("KWT", "KWT", "19:00", "KWT", 1)
            db.save_expected_flux("SPG", "SPG", "20:00", "SPG", 1)
            db.save_expected_flux("KSA", "KSA", "21:00", "KSA", 1)
            logging.getLogger(__name__).info("Seed expected_flux initialisé.")
    except Exception as e:
        logging.getLogger(__name__).warning("Impossible de seeder expected_flux: %s", e)



# ✅ HORS du if __name__ → exécuté par gunicorn aussi
# ⚠️  Must be wrapped in try/except: if bootstrap fails (e.g. Azure SQL down),
#     gunicorn returns 502. The app can still serve static files and health checks.
try:
    with app.app_context():
        _bootstrap()
        setup_azure_monitoring(app)
        try:
            from core.sla_monitor import init_sla_scheduler
            from storage import get_storage
            # Infra switch: when a dedicated scheduler container runs
            # scheduler_worker.py, set FLASK_EMBEDDED_SCHEDULER=0 so the
            # web tier does not start a second SLA monitor (duplicate alerts).
            if os.environ.get("FLASK_EMBEDDED_SCHEDULER", "1") != "0":
                init_sla_scheduler(app, get_storage())
                logging.getLogger(__name__).info(
                    "Scheduler SLA dynamique démarré (core/sla_monitor — remplace scheduler 4h fixe)"
                )
            else:
                logging.getLogger(__name__).info(
                    "Scheduler SLA embarqué désactivé (FLASK_EMBEDDED_SCHEDULER=0) — délégué au conteneur scheduler"
                )
        except Exception as se:
            logging.getLogger(__name__).error("Impossible de démarrer le planificateur SLA: %s", se)

        # Optionally start local queue worker
        try:
            from config import settings
            if settings.ENABLE_SCHEDULER and settings.QUEUE_BACKEND == "local":
                from core.local_worker import start_local_worker
                start_local_worker(get_storage())
                logging.getLogger(__name__).info("Local queue worker démarré (QUEUE_BACKEND=local)")
        except Exception as lw_err:
            logging.getLogger(__name__).warning("Local worker non démarré: %s", lw_err)
except Exception as boot_error:
    logging.getLogger(__name__).critical(
        "⚠️  Échec du bootstrap — l'application démarre en mode dégradé : %s", boot_error
    )
    # Fail-closed : en production, un bootstrap incomplet = refus de démarrer.
    if settings.is_production:
        raise

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
