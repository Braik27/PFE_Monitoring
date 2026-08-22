"""
Politique SLA par criticité / type d'écart (CEGID vs Oracle).

Grille P1/P2/P3 — voir SLA_DIAGNOSTIC.md
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple


SLA_THRESHOLDS = {
    "critical_max": 70.0,   # concordance < 70% → CRITIQUE
    "warning_max": 80.0,    # 70% <= concordance < 80% → ATTENTION
}
SLA_HOURS = {
    "CRITICAL": 2.0,        # 2h SLA for CRITIQUE (0-70%)
    "WARNING": 3.0,         # 3h SLA for ATTENTION (>70%-<80%)
    "NORMAL": 4.0,          # 4h SLA for NORMAL (>=80%)
}

# Allowed SLA durations: 2h, 3h, 4h ONLY. Max = 4h.
ALLOWED_SLA_HOURS = frozenset({2.0, 3.0, 4.0})
MAX_SLA_HOURS = 4.0

# Legacy P1/P2/P3 grid (kept for backward compat and special cases like FICHIER_MANQUANT)
SLA_HOURS_BY_CLASS = {
    "P1_FILE": 2.0,       # FICHIER_MANQUANT
    "P1_MASS": 4.0,       # Manquants massifs (>10% lignes)
    "P2": 4.0,            # Écarts montants / critiques standard
    "P3": 4.0,            # Warnings format (forced to 4h max)
}


def classify_by_concordance(
    concordance_pct: float,
    flux_config: Optional[dict] = None,
) -> Tuple[Optional[str], float]:
    """
    Classify alert severity and SLA hours based on concordance rate.

    Args:
        concordance_pct: Conformity rate (0.0 to 100.0)
        flux_config: Optional per-flux override dict with keys
                     'critical_max', 'warning_max', 'sla_hours'

    Returns:
        (severity, sla_hours) or (None, 0.0) if concordance >= 80%
    """
    cfg = SLA_THRESHOLDS.copy()
    hours = SLA_HOURS.copy()
    if flux_config:
        cfg.update({k: v for k, v in flux_config.items() if k in cfg})
        if "sla_hours" in flux_config:
            hours.update(flux_config["sla_hours"])

    if concordance_pct < cfg["critical_max"]:
        return "CRITICAL", hours.get("CRITICAL", 2.0)
    elif concordance_pct < cfg["warning_max"]:
        return "WARNING", hours.get("WARNING", 3.0)
    else:
        return None, 0.0


def get_concordance_state(concordance_pct: float) -> str:
    """
    Return human-readable concordance state.
    0-70% = CRITIQUE, >70%-<80% = ATTENTION, >=80% = NORMAL
    """
    if concordance_pct < SLA_THRESHOLDS["critical_max"]:
        return "CRITIQUE"
    elif concordance_pct < SLA_THRESHOLDS["warning_max"]:
        return "ATTENTION"
    return "NORMAL"


def validate_sla_hours(sla_hours: float) -> None:
    """
    Validate that SLA hours is within allowed range (2h, 3h, 4h).
    Raises ValueError if invalid.
    """
    if sla_hours > 4.0:
        raise ValueError("La durée maximale autorisée pour un SLA est de 4 heures.")
    if sla_hours not in (2.0, 3.0, 4.0):
        raise ValueError("La durée du SLA doit être de 2h, 3h ou 4h uniquement.")


def _anomaly_types(anomalies: List[dict]) -> set:
    types = set()
    for a in anomalies or []:
        t = (a.get("error_type") or a.get("type_ecart") or "").upper()
        if t:
            types.add(t)
    return types


def _is_amount_error(error_type: str) -> bool:
    t = error_type.lower()
    return any(k in t for k in (
        "montant", "prix", "amount", "invoice", "ecart_invoice",
        "ecart_montant", "prix_different", "montant_different",
    ))


def _is_format_warning(error_type: str) -> bool:
    t = error_type.upper()
    if "HEADER_ID" in t or "TRONQU" in t:
        return True
    return t.startswith("ECART_") and any(
        a.get("severity", "").upper() in ("WARNING", "WARN")
        for a in []
    )


def classify_alert(
    anomalies: List[dict],
    n_critiques: int = 0,
    n_warnings: int = 0,
    concordance: float = 100.0,
    comparison_stats: Optional[dict] = None,
) -> Tuple[str, float, str]:
    """
    Retourne (severity_class, sla_hours, flux_type_hint).

    severity_class: P1_FILE | P1_MASS | P2 | P3
    flux_type_hint: code métier pour reporting (missing_file, mass_gap, amount, format, default)
    """
    types = _anomaly_types(anomalies)

    if "FICHIER_MANQUANT" in types:
        return "P1_FILE", SLA_HOURS_BY_CLASS["P1_FILE"], "missing_file"

    stats = comparison_stats or {}
    n_cegid = stats.get("nb_lignes_cegid") or stats.get("n_cegid") or 0
    n_oracle = stats.get("nb_lignes_oracle") or stats.get("n_oracle") or 0
    n_base = max(int(n_cegid), int(n_oracle), 1)
    n_missing = stats.get("nb_absents_oracle", 0) + stats.get("nb_absents_cegid", 0)
    if not n_missing:
        n_missing = sum(
            1 for a in (anomalies or [])
            if (a.get("error_type") or "").upper() in ("MANQUANT_ORACLE", "MANQUANT_CEGID", "ABSENT_ORACLE", "ABSENT_CEGID")
        )

    if n_missing / n_base > 0.10:
        return "P1_MASS", SLA_HOURS_BY_CLASS["P1_MASS"], "mass_gap"

    for t in types:
        if _is_amount_error(t):
            return "P2", SLA_HOURS_BY_CLASS["P2"], "amount"

    if n_critiques > 0:
        return "P2", SLA_HOURS_BY_CLASS["P2"], "critical"

    # Warnings seuls (format, HEADER_ID, etc.)
    if n_warnings > 0 or types:
        for t in types:
            if "HEADER" in t or t.startswith("ECART_"):
                sev = next(
                    (a.get("severity", "") for a in (anomalies or []) if (a.get("error_type") or "").upper() == t),
                    "",
                )
                if sev.upper() in ("WARNING", "WARN") or n_critiques == 0:
                    return "P3", SLA_HOURS_BY_CLASS["P3"], "format"
        return "P3", SLA_HOURS_BY_CLASS["P3"], "format"

    return "P2", SLA_HOURS_BY_CLASS["P2"], "default"


def compute_sla_at_creation(
    created_at: datetime,
    severity_class: str,
    sla_hours: float,
) -> Dict[str, Any]:
    """Calcule sla_deadline et remaining_pct à la création."""
    deadline = created_at + timedelta(hours=sla_hours)
    return {
        "sla_deadline": deadline.isoformat(),
        "sla_hours": round(sla_hours, 1),
        "remaining_pct": 100.0,
        "breached": False,
        "severity_class": severity_class,
    }


def parse_expected_hour_today(expected_hour: str, ref: Optional[datetime] = None) -> Optional[datetime]:
    """Convertit '18:00' en datetime du jour (UTC naïf)."""
    if not expected_hour or ":" not in expected_hour:
        return None
    ref = ref or datetime.utcnow()
    try:
        h, m = map(int, expected_hour.strip().split(":")[:2])
        return ref.replace(hour=h, minute=m, second=0, microsecond=0)
    except (ValueError, TypeError):
        return None


def compute_detection_latency_minutes(
    detected_at: datetime,
    expected_hour: str,
) -> Optional[float]:
    """Minutes entre heure métier attendue et détection réelle (peut être négatif si en avance)."""
    expected = parse_expected_hour_today(expected_hour, detected_at)
    if not expected:
        return None
    return round((detected_at - expected).total_seconds() / 60.0, 1)


# Statuts surveillés pour dépassement SLA (explicite — ne pas élargir sans revue)
SLA_MONITORED_STATUSES = frozenset({
    "NEW", "PENDING", "ACKNOWLEDGED", "IN_PROGRESS", "ESCALATED",
})

# Statuts exclus du scan SLA — jamais de breach email ni flag pour ceux-ci
SLA_EXCLUDED_STATUSES = frozenset({
    "IGNORED", "RESOLVED", "CLOSED",
})


def get_expected_hour_for_flux(flux_id: str) -> str:
    """Heure limite métier depuis expected_flux ou registry JSON."""
    try:
        from storage import get_storage
        for row in get_storage().list_expected_flux() or []:
            if row.get("flux_id") == flux_id and row.get("expected_hour"):
                return str(row["expected_hour"])
    except Exception:
        pass
    try:
        from engine.flux_loader import FluxLoader
        cfg = FluxLoader.load(flux_id)
        return getattr(cfg, "expected_hour", "") or ""
    except Exception:
        pass
    return ""


def build_sla_meta(
    anomalies: List[dict],
    n_critiques: int = 0,
    n_warnings: int = 0,
    concordance: float = 100.0,
    comparison_stats: Optional[dict] = None,
    expected_hour: str = "",
    detected_at: Optional[datetime] = None,
    flux_config: Optional[dict] = None,
) -> Dict[str, Any]:
    """Métadonnées SLA à persister à la création d'une alerte."""
    detected_at = detected_at or datetime.utcnow()

    severity, sla_hrs = classify_by_concordance(concordance, flux_config)

    if severity is None or severity == "NORMAL":
        severity_class = "NORMAL"
        sla_hours_val = 4.0
        flux_type = "default"
        severity = ""
    else:
        severity_class = severity
        sla_hours_val = sla_hrs
        flux_type = "critical" if severity == "CRITICAL" else "warning"

    sla = compute_sla_at_creation(detected_at, severity_class, sla_hours_val)
    latency = (
        compute_detection_latency_minutes(detected_at, expected_hour)
        if expected_hour else None
    )
    concordance_state = get_concordance_state(concordance)
    return {
        "sla_deadline": sla["sla_deadline"],
        "sla_hours": sla["sla_hours"],
        "remaining_pct": sla["remaining_pct"],
        "flux_type": flux_type,
        "severity_class": severity_class,
        "severity": severity or "",
        "detected_at": detected_at.isoformat(),
        "expected_hour": expected_hour or None,
        "detection_latency_minutes": latency,
        "concordance_state": concordance_state,
    }


def parse_alert_datetime(date_str) -> datetime:
    """Parse robuste des dates ISO/SQL ou objets datetime."""
    if isinstance(date_str, datetime):
        return date_str
    if hasattr(date_str, 'isoformat') and callable(getattr(date_str, 'isoformat')):
        # In case of pandas Timestamp or other date-like objects
        try:
            return datetime.fromisoformat(date_str.isoformat().replace("Z", ""))
        except Exception:
            return date_str
    if not date_str or not isinstance(date_str, str):
        return datetime.utcnow()
    clean = date_str.split(".")[0].replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(clean[:19], fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(date_str.replace("Z", ""))
    except ValueError:
        return datetime.utcnow()



def recompute_sla_progress(alert: dict) -> Dict[str, Any]:
    """Recalcule remaining_pct et breached depuis sla_deadline stocké."""
    sla_hours = float(alert.get("sla_hours") or 4.0)
    created = parse_alert_datetime(alert.get("created_at", ""))
    deadline_str = alert.get("sla_deadline")
    if deadline_str:
        deadline = parse_alert_datetime(deadline_str)
    else:
        deadline = created + timedelta(hours=sla_hours)

    now = datetime.utcnow()
    total_sec = sla_hours * 3600
    if total_sec <= 0:
        remaining_pct = 0.0
    else:
        remaining_sec = max(0, (deadline - now).total_seconds())
        remaining_pct = round(remaining_sec / total_sec * 100, 1)

    return {
        "sla_deadline": deadline.isoformat(),
        "sla_hours": sla_hours,
        "remaining_pct": remaining_pct,
        "breached": now > deadline,
    }
