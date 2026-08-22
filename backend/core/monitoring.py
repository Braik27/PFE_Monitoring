"""
core/monitoring.py
==================
Intégration Azure Application Insights avec la librairie officielle 2024/2025.

⚠️  IMPORTANT : Ne pas utiliser opencensus-ext-azure (déprécié depuis 2023).
    La librairie correcte est : azure-monitor-opentelemetry

    Pour l'installer : ajouter dans requirements.txt
        azure-monitor-opentelemetry>=1.6.0

Fonctions exposées :
  - setup_azure_monitoring(app)   → à appeler une seule fois au démarrage dans app.py
  - track_event(name, properties) → envoie un événement custom dans App Insights
  - track_exception(exc)          → envoie une exception dans App Insights
"""

from __future__ import annotations
import logging, os, json, time, pathlib, threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# MÉTRIQUES LOCALES (fonctionnent SANS App Insights, stockées en JSON)
# Utilisées par /api/system/metrics pour les consultants
# ─────────────────────────────────────────────────────────────────────────────

import tempfile
_METRICS_DIR = pathlib.Path(tempfile.gettempdir())
_METRICS_FILE = _METRICS_DIR / "metrics.json"
_lock = threading.RLock()

_metrics_cache = {
    "requests_total": 0,
    "requests_error": 0,
    "response_times_ms": [],
    "ia_calls_total": 0,
    "ia_calls_success": 0,
    "last_reset": datetime.now().isoformat(),
    "_dirty": False,
}

_METRICS_FLUSH_INTERVAL = 60
_last_flush = [time.time()]


def _load_metrics() -> dict:
    return _metrics_cache


def _save_metrics(data: dict) -> None:
    pass


def _flush_metrics():
    if not _metrics_cache.get("_dirty"):
        return
    try:
        _METRICS_DIR.mkdir(exist_ok=True)
        data = {k: v for k, v in _metrics_cache.items() if k != "_dirty"}
        with open(_METRICS_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _metrics_cache["_dirty"] = False
    except Exception as e:
        logger.debug("Metrics flush failed: %s", e)


def _maybe_flush():
    now = time.time()
    if now - _last_flush[0] >= _METRICS_FLUSH_INTERVAL:
        _flush_metrics()
        _last_flush[0] = now


def _init_from_disk():
    try:
        with open(_METRICS_FILE) as f:
            disk = json.load(f)
        _metrics_cache["requests_total"] = disk.get("requests_total", 0)
        _metrics_cache["requests_error"] = disk.get("requests_error", 0)
        _metrics_cache["response_times_ms"] = disk.get("response_times_ms", [])
        _metrics_cache["ia_calls_total"] = disk.get("ia_calls_total", 0)
        _metrics_cache["ia_calls_success"] = disk.get("ia_calls_success", 0)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

_init_from_disk()


def record_request(duration_ms: float, is_error: bool = False) -> None:
    with _lock:
        _metrics_cache["requests_total"] += 1
        if is_error:
            _metrics_cache["requests_error"] += 1
        times = _metrics_cache["response_times_ms"]
        times.append(round(duration_ms, 1))
        if len(times) > 1000:
            _metrics_cache["response_times_ms"] = times[-1000:]
        _metrics_cache["_dirty"] = True
    _maybe_flush()


def record_ia_call(success: bool) -> None:
    with _lock:
        _metrics_cache["ia_calls_total"] += 1
        if success:
            _metrics_cache["ia_calls_success"] += 1
        _metrics_cache["_dirty"] = True
    _maybe_flush()


def get_metrics_summary() -> dict:
    m = _metrics_cache
    total = m.get("requests_total", 0)
    errors = m.get("requests_error", 0)
    times = m.get("response_times_ms", [])
    ia_total = m.get("ia_calls_total", 0)
    ia_success = m.get("ia_calls_success", 0)

    return {
        "requests_total":    total,
        "error_rate_pct":    round(errors / total * 100, 1) if total > 0 else 0,
        "avg_response_ms":   round(sum(times) / len(times), 0) if times else 0,
        "p95_response_ms":   _percentile(times, 95) if times else 0,
        "ia_success_rate_pct": round(ia_success / ia_total * 100, 1) if ia_total > 0 else 100,
        "ia_calls_total":    ia_total,
    }


def flush_metrics_now():
    with _lock:
        _flush_metrics()


def _percentile(data: list, pct: int) -> float:
    if not data:
        return 0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * pct / 100)
    return sorted_data[min(idx, len(sorted_data) - 1)]


# ─────────────────────────────────────────────────────────────────────────────
# WATCHER HEARTBEAT
# watcher_scheduler.py appelle write_heartbeat() toutes les minutes
# system_status.py lit ce fichier pour savoir si le watcher tourne
# ─────────────────────────────────────────────────────────────────────────────

def write_heartbeat() -> None:
    """Appelé par trigger/watcher_scheduler.py à chaque itération."""
    _METRICS_DIR.mkdir(exist_ok=True)
    hb_path = _METRICS_DIR / "watcher_heartbeat.json"
    with _lock:
        try:
            existing = json.loads(hb_path.read_text()) if hb_path.exists() else {}
        except Exception:
            existing = {}
        existing.update({
            "last_beat": datetime.now().isoformat(),
            "total_checks": existing.get("total_checks", 0) + 1,
            "failed_checks": existing.get("failed_checks", 0),
        })
        hb_path.write_text(json.dumps(existing, indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# AZURE APPLICATION INSIGHTS — SDK officiel 2024/2025
# Nécessite : pip install azure-monitor-opentelemetry>=1.6.0
# ─────────────────────────────────────────────────────────────────────────────

def setup_azure_monitoring(app) -> bool:
    """
    Configure Azure Application Insights avec la librairie officielle 2024.
    Appeler UNE SEULE FOIS dans app.py après la création de l'app Flask.

    Retourne True si l'activation a réussi, False sinon.

    Ce qui est automatiquement capturé après activation :
      ✅ Toutes les requêtes HTTP Flask (URL, statut, durée)
      ✅ Toutes les exceptions Python non gérées (stacktrace complète)
      ✅ Tous les appels logging.warning() et logging.error()
      ✅ Dépendances externes (SQLite, HTTP vers Ollama)
      ✅ Métriques temps réel dans Azure Portal → Live Metrics
    """
    conn_str = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn_str:
        app.logger.warning(
            "⚠️  APPLICATIONINSIGHTS_CONNECTION_STRING non trouvée — "
            "monitoring Azure désactivé. Métriques locales JSON actives."
        )
        return False

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        configure_azure_monitor(connection_string=conn_str)
        app.logger.info("✅ Azure Application Insights activé — monitoring opérationnel")
        app.logger.info("📊 Toutes les requêtes, exceptions et logs sont tracés dans Azure Portal")
        return True

    except ImportError:
        app.logger.warning(
            "📦 azure-monitor-opentelemetry non installé.\n"
            "   Pour activer Application Insights, ajouter dans requirements.txt :\n"
            "   azure-monitor-opentelemetry>=1.6.0\n"
            "   puis: git add . && git commit -m 'feat: App Insights' && git push"
        )
        return False

    except Exception as e:
        app.logger.error(f"❌ Erreur initialisation Application Insights : {e}")
        return False


def track_event(name: str, properties: Optional[dict] = None) -> None:
    """
    Envoie un événement custom dans Application Insights.

    Exemples d'utilisation :
        track_event("ecart_detecte", {"flux": "CEGID_Q1", "critiques": 5})
        track_event("ia_suggestion_acceptee", {"type_ecart": "MANQUANT_ORACLE"})
        track_event("alerte_resolue", {"temps_resolution_minutes": 25})

    Ces événements apparaissent dans Azure Portal → Application Insights → Events.
    """
    try:
        from opentelemetry import trace
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span(name) as span:
            if properties:
                for k, v in properties.items():
                    span.set_attribute(str(k), str(v))
    except Exception:
        # Ne jamais crasher si le monitoring échoue
        logger.debug(f"track_event ignoré (App Insights non configuré) : {name}")


def track_exception(exc: Exception, properties: Optional[dict] = None) -> None:
    """
    Envoie une exception dans Application Insights avec contexte.

    Exemple :
        try:
            run_analysis(...)
        except Exception as e:
            track_exception(e, {"flux_id": flux_id, "user": username})
            raise
    """
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        span.record_exception(exc)
        if properties:
            for k, v in properties.items():
                span.set_attribute(str(k), str(v))
    except Exception:
        logger.debug(f"track_exception ignoré : {exc}")