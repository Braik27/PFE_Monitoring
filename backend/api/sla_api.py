"""API conformité SLA — GET /api/sla/metrics"""

import logging
from flask import Blueprint, jsonify, request

from api.auth import require_auth
from storage import get_storage

log = logging.getLogger(__name__)

sla_bp = Blueprint("sla", __name__)


@sla_bp.get("/api/sla/metrics")
@require_auth
def sla_metrics():
    """
    KPIs de conformité SLA :
    - compliance_pct, mttr_hours, current_breaches
    - trend_7d / trend_30d (compliance, MTTR, ignored_count)
    """
    days = int(request.args.get("days", 30))
    days = max(1, min(days, 365))
    try:
        metrics = get_storage().get_sla_metrics(days=days)
        return jsonify(metrics)
    except Exception as exc:
        log.exception("[SLA API] Erreur métriques : %s", exc)
        return jsonify({"error": str(exc)}), 500
