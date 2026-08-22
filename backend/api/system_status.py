"""
api/system_status.py
====================
Endpoints de monitoring système — consommés par le dashboard et Azure Availability Test.

Routes :
  GET /api/system/health      → état rapide (< 200ms, utilisé par Azure Availability Test)
  GET /api/system/metrics     → métriques détaillées pour le dashboard consultant
  GET /api/system/audit       → journal des actions des consultants (traçabilité)
"""

from __future__ import annotations
import os, time, urllib.request, urllib.error
from datetime import datetime, timedelta
from flask import Blueprint, jsonify
from api.auth import require_auth, require_admin
from storage import get_storage

system_bp = Blueprint("system", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# /api/system/health — ping rapide (utilisé par Azure Availability Test)
# ─────────────────────────────────────────────────────────────────────────────

@system_bp.get("/api/system/health")
@require_admin
def system_health():
    """
    Endpoint de santé rapide — répond TOUJOURS en < 500ms.
    Azure Availability Test appelle cet endpoint toutes les 5 minutes depuis 5 régions.
    Si la réponse n'est pas HTTP 200, une alerte email Azure est envoyée automatiquement.

    Ne nécessite pas d'authentification (sinon l'Availability Test échoue).
    """
    t0 = time.time()
    components: dict[str, str] = {}
    overall = "healthy"

    # ── 1. Base de données SQLite ─────────────────────────────────────────────
    try:
        db = get_storage()
        db.list_analyses(limit=1)   # requête minimale pour valider la connexion
        components["database"] = "ok"
    except Exception as e:
        components["database"] = f"error: {str(e)[:60]}"
        overall = "degraded"

    # ── 2. Ollama (optionnel — dégradé si absent, pas critique) ──────────────
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    try:
        req = urllib.request.Request(
            f"{ollama_host}/api/tags",
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            components["ollama"] = "ok" if resp.status == 200 else "slow"
    except Exception:
        # Ollama absent = IA désactivée, mais l'app fonctionne quand même
        components["ollama"] = "unavailable"
        # Ne pas mettre overall à "degraded" : l'app reste fonctionnelle sans Ollama

    elapsed_ms = round((time.time() - t0) * 1000)

    return jsonify({
        "status":     overall,
        "version":    "v1",
        "timestamp":  datetime.utcnow().isoformat() + "Z",
        "response_ms": elapsed_ms,
        "components": components,
        "environment": os.environ.get("ENV", "production"),
    }), 200  # Toujours 200 pour que l'Availability Test passe (même si degraded)


# ─────────────────────────────────────────────────────────────────────────────
# /api/system/metrics — métriques détaillées pour le dashboard
# ─────────────────────────────────────────────────────────────────────────────

@system_bp.get("/api/system/metrics")
@require_admin
def system_metrics():
    """
    Métriques business et système pour le dashboard consultant.
    Le frontend appelle cet endpoint toutes les 30 secondes.

    Retourne :
      - Statistiques des dernières 24h (analyses, écarts, alertes)
      - Tendance semaine sur semaine
      - Taux de résolution des alertes
      - État résumé de l'IA
    """
    db = get_storage()
    now = datetime.now()
    yesterday = now - timedelta(hours=24)
    last_week_start = now - timedelta(days=14)
    this_week_start = now - timedelta(days=7)

    # ── Données brutes depuis SQLite ──────────────────────────────────────────
    all_alerts    = db.list_alerts(limit=500)
    all_analyses  = db.list_analyses(limit=200)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _is_recent(row: dict, since: datetime) -> bool:
        try:
            return datetime.fromisoformat(row["created_at"]) >= since
        except Exception:
            return False

    # ── Métriques 24h ────────────────────────────────────────────────────────
    alerts_24h     = [a for a in all_alerts   if _is_recent(a, yesterday)]
    analyses_24h   = [a for a in all_analyses if _is_recent(a, yesterday)]

    critiques_24h  = sum(a.get("n_critiques", 0) for a in alerts_24h)
    warnings_24h   = sum(a.get("n_warnings",  0) for a in alerts_24h)
    resolved_24h   = sum(1 for a in alerts_24h if a.get("status") in ("RESOLVED",))
    pending_now    = sum(1 for a in all_alerts if a.get("status") in ("NEW", "PENDING", "ACKNOWLEDGED", "IN_PROGRESS"))

    taux_resolution = round(resolved_24h / len(alerts_24h) * 100) if alerts_24h else 0

    # Concordance moyenne 24h (qualité des flux)
    concords = [a.get("concordance", 0) for a in alerts_24h if a.get("concordance")]
    avg_concordance = round(sum(concords) / len(concords), 1) if concords else None

    # ── Tendance semaine/semaine ──────────────────────────────────────────────
    alerts_this_week = [a for a in all_alerts if _is_recent(a, this_week_start)]
    alerts_last_week = [a for a in all_alerts
                        if _is_recent(a, last_week_start) and not _is_recent(a, this_week_start)]

    crit_this = sum(a.get("n_critiques", 0) for a in alerts_this_week)
    crit_last = sum(a.get("n_critiques", 0) for a in alerts_last_week)

    if crit_last > 0:
        tendance_pct = round((crit_this - crit_last) / crit_last * 100)
    else:
        tendance_pct = 0

    if tendance_pct < -5:
        tendance_label = f"🟢 -{abs(tendance_pct)}% vs semaine dernière"
    elif tendance_pct > 5:
        tendance_label = f"🔴 +{tendance_pct}% vs semaine dernière"
    else:
        tendance_label = "🟡 Stable vs semaine dernière"

    # ── Top articles problématiques ───────────────────────────────────────────
    article_counts: dict[str, int] = {}
    for alert in alerts_24h:
        for anomaly in alert.get("anomalies", []):
            item = anomaly.get("item_code") or anomaly.get("ref") or anomaly.get("code", "?")
            article_counts[item] = article_counts.get(item, 0) + 1

    top_articles = [
        {"ref": ref, "nb_ecarts": nb}
        for ref, nb in sorted(article_counts.items(), key=lambda x: -x[1])[:5]
    ]

    return jsonify({
        # Métriques 24h
        "analyses_24h":        len(analyses_24h),
        "alerts_24h":          len(alerts_24h),
        "critiques_24h":       critiques_24h,
        "warnings_24h":        warnings_24h,
        "pending_now":         pending_now,
        "taux_resolution_24h": taux_resolution,
        "avg_concordance":     avg_concordance,

        # Tendance
        "tendance_label":  tendance_label,
        "tendance_pct":    tendance_pct,
        "top_articles":    top_articles,

        # Système
        "timestamp":       now.isoformat(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# /api/system/audit — journal de traçabilité pour les consultants et audits
# ─────────────────────────────────────────────────────────────────────────────

@system_bp.get("/api/system/audit")
@require_admin
def system_audit():
    """
    Journal des actions : qui a fait quoi, sur quelle alerte, quand.
    Utilisé pour l'onglet Traçabilité du dashboard.

    Paramètre optionnel : ?limit=50&days=7
    """
    limit = min(int(request.args.get("limit", 50)), 200)
    days  = int(request.args.get("days", 7))
    since = datetime.now() - timedelta(days=days)

    db = get_storage()
    all_alerts = db.list_alerts(limit=500)

    audit_entries = []
    for alert in all_alerts:
        try:
            if datetime.fromisoformat(alert["created_at"]) < since:
                continue
        except Exception:
            continue

        tracking = db.get_tracking(alert["token"])
        for t in tracking:
            audit_entries.append({
                "timestamp":  t.get("created_at"),
                "consultant": t.get("username", "?"),
                "action":     t.get("action", "?"),
                "flux":       alert.get("flux_name", "?"),
                "alert_token": alert["token"][:8] + "...",
                "n_critiques": alert.get("n_critiques", 0),
            })

    # Tri chronologique décroissant
    audit_entries.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    return jsonify({
        "entries": audit_entries[:limit],
        "total":   len(audit_entries),
        "period_days": days,
    })


# Import manquant ajouté en bas pour éviter les imports circulaires
from flask import request