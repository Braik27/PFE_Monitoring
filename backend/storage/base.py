from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, List, Optional
import json
import math

# Vérifie si numpy est disponible
_HAS_NUMPY = False
np = None  # type: ignore

def _ensure_numpy():
    global _HAS_NUMPY, np
    if np is not None:
        return
    try:
        import numpy as np_mod
        np = np_mod
        _HAS_NUMPY = True
    except ImportError:  # pragma: no cover
        np = None  # type: ignore
        _HAS_NUMPY = False


def _clean_nan(val):
    """
    Recursively replaces NaN/Inf floats with None in nested dicts/lists.
    NumPy NaN is also a float('nan') and needs this cleaning.
    """
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    elif isinstance(val, dict):
        return {k: _clean_nan(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [_clean_nan(item) for item in val]
    elif isinstance(val, tuple):
        return tuple(_clean_nan(item) for item in val)
    return val


class RobustJSONEncoder(json.JSONEncoder):
    """
    Pourquoi : Azure B1 → MemoryError / TypeError sur json.dumps()
    Quand   : les DataFrames génèrent des np.int64, np.float64, pd.Timestamp, NaN, NaT
    """
    def default(self, obj: Any) -> Any:
        _ensure_numpy()
        if _HAS_NUMPY and isinstance(obj, (np.integer,)):
            return int(obj)
        if _HAS_NUMPY and isinstance(obj, np.floating):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return float(obj)
        if _HAS_NUMPY and isinstance(obj, np.ndarray):
            return obj.tolist()
        if _HAS_NUMPY and isinstance(obj, np.bool_):
            return bool(obj)
        if _HAS_NUMPY and isinstance(obj, np.dtype):
            return str(obj)
        # Pandas Timestamp / NaT
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return super().default(obj)


def json_encode(obj: Any) -> str:
    """
    Wrapper robuste pour json.dumps.
    Gère np.int64, np.float64, NaN, NaT, datetime, etc.
    """
    cleaned_obj = _clean_nan(obj)
    return json.dumps(cleaned_obj, ensure_ascii=False, cls=RobustJSONEncoder)


class BaseStorage(ABC):
    @abstractmethod
    def init_db(self): ...
    
    # Analyses
    @abstractmethod
    def save_analysis(self, flux_id, label, summary) -> int: ...
    @abstractmethod
    def get_analysis(self, analysis_id) -> Optional[dict]: ...
    @abstractmethod
    def list_analyses(self, flux_id=None, limit=50) -> List[dict]: ...
    @abstractmethod
    def count_analyses(self, flux_id=None) -> int: ...
    @abstractmethod
    def count_analyses_by_analyst(self, username: str) -> int: ...
    @abstractmethod
    def delete_analysis(self, analysis_id): ...
    @abstractmethod
    def update_summary(self, analysis_id, summary): ...
    
    # Users
    @abstractmethod
    def save_user(self, username, password_hash, role) -> int: ...
    @abstractmethod
    def get_user(self, username) -> Optional[dict]: ...
    @abstractmethod
    def get_user_by_id(self, user_id: int) -> Optional[dict]: ...
    @abstractmethod
    def list_users(self) -> List[dict]: ...
    @abstractmethod
    def get_user_by_email(self, email: str) -> Optional[dict]: ...
    @abstractmethod
    def update_user_profile(self, user_id: int, **kwargs): ...
    @abstractmethod
    def update_user_password(self, user_id: int, password_hash: str): ...
    @abstractmethod
    def update_reset_token(self, user_id: int, token: Optional[str], expires_at: Optional[str]): ...
    @abstractmethod
    def get_user_by_reset_token(self, token: str) -> Optional[dict]: ...
    @abstractmethod
    def update_user_status(self, user_id: int, active: int): ...
    @abstractmethod
    def update_user(self, user_id: int, **kwargs): ...
    @abstractmethod
    def delete_user(self, user_id: int): ...

    # Divisions
    @abstractmethod
    def list_divisions(self) -> List[dict]: ...
    @abstractmethod
    def get_division(self, code: str) -> Optional[dict]: ...
    @abstractmethod
    def save_division(self, code: str, name: str, country: str, flag: str) -> int: ...
    @abstractmethod
    def delete_division(self, code: str): ...

    # Alerts & Tracking
    @abstractmethod
    def save_alert(self, token: str, analysis_id: int, flux_id: str,
                   flux_name: str, label: str, n_critiques: int,
                   n_warnings: int, concordance: float,
                   anomalies: list, email_sent_to: str = "",
                   sla_meta: Optional[dict] = None) -> int: ...
    @abstractmethod
    def get_alert_by_token(self, token: str) -> Optional[dict]: ...
    def get_alert_by_token_prefix(self, prefix: str) -> Optional[dict]:
        """Find alert by token prefix. Default implementation uses list_alerts."""
        for a in self.list_alerts(limit=200):
            if a.get("token", "").startswith(prefix):
                return self.get_alert_by_token(a["token"])
        return None
    @abstractmethod
    def list_alerts(self, flux_id=None, limit=50, status_not_in=None) -> List[dict]: ...
    @abstractmethod
    def delete_alert(self, token: str) -> None:
        """Supprime une alerte et ses données associées (tracking, historique, feedback)."""
    @abstractmethod
    def update_alert_status(self, token: str, status: str, **kwargs) -> None: ...
    @abstractmethod
    def update_sla_fields(self, token: str, sla_data: dict) -> None:
        """
        Update SLA-related fields: sla_deadline, sla_hours, remaining_pct, breached.
        
        Args:
            token: Alert token
            sla_data: dict with keys 'sla_deadline', 'sla_hours', 'remaining_pct', 'breached'
        """
    @abstractmethod
    def save_tracking(self, alert_token: str, username: str,
                      action: str, comment: str = "") -> int: ...
    @abstractmethod
    def get_tracking(self, alert_token: str) -> List[dict]: ...
    @abstractmethod
    def flag_sla_breached(self, token: str) -> None: ...
    @abstractmethod
    def update_sla_status(self, token: str, sla_status: str, audit_username: str = "system") -> None: ...
    @abstractmethod
    def set_breach_email_sent(self, token: str) -> None: ...
    @abstractmethod
    def set_breach_report_sent(self, token: str) -> None: ...
    @abstractmethod
    def set_sla_warning_sent(self, token: str) -> None: ...
    @abstractmethod
    def set_ignore_notification_sent(self, token: str) -> None: ...
    @abstractmethod
    def set_resolved(self, token: str, username: str) -> None: ...
    @abstractmethod
    def set_escalated(self, token: str, by_user: str, to_email: str) -> None: ...
    @abstractmethod
    def get_users_for_flux(self, flux_id: str) -> List[dict]: ...

    # Correction history
    @abstractmethod
    def save_correction(self, flux_id: str, error_type: str,
                        column_name: str, solution_applied: str,
                        was_effective: bool = True) -> int: ...
    @abstractmethod
    def get_similar_corrections(self, flux_id: str, error_type: str,
                                column_name: str = "", limit: int = 5) -> List[dict]: ...

    # Smart Mappings
    @abstractmethod
    def save_smart_mapping(self, flux_key: str, cegid_col: str, oracle_col: str, username: str) -> None: ...
    @abstractmethod
    def load_learned_mapping(self, flux_key: str) -> dict: ...
    @abstractmethod
    def list_smart_mappings(self) -> List[dict]: ...

    # Assistant conversations
    @abstractmethod
    def create_conversation(self, user_id: str, title: str) -> int: ...
    @abstractmethod
    def get_conversation(self, conv_id: int, user_id: str) -> Optional[dict]: ...
    @abstractmethod
    def list_conversations(self, user_id: str, limit: int = 20) -> List[dict]: ...
    @abstractmethod
    def delete_conversation(self, conv_id: int, user_id: str) -> None: ...
    @abstractmethod
    def save_message(self, conv_id: int, role: str, content: str, context_keys: list = None) -> None: ...
    @abstractmethod
    def get_conversation_messages(self, conv_id: int, limit: int = 40) -> List[dict]: ...
    @abstractmethod
    def get_conversation_summary(self, conv_id: int) -> str: ...
    @abstractmethod
    def update_conversation_summary(self, conv_id: int, summary: str) -> None: ...
    @abstractmethod
    def save_user_pattern(self, user_id: str, pattern: str) -> None: ...
    @abstractmethod
    def get_user_patterns(self, user_id: str, limit: int = 5) -> List[dict]: ...

    # Ecarts (legacy)
    @abstractmethod
    def save_ecarts(self, ecarts: list) -> None: ...
    @abstractmethod
    def list_ecarts(self, flux_id: str, limit: int = 100) -> List[dict]: ...
    @abstractmethod
    def update_ecart_status(self, ecart_id: int, status: str) -> None: ...

    # Persistent Jobs
    @abstractmethod
    def save_job(self, job_id: str, job_type: str, status: str, progress: int, step_label: str, meta: dict = None) -> None: ...
    @abstractmethod
    def update_job(self, job_id: str, **kwargs) -> None: ...
    @abstractmethod
    def get_job(self, job_id: str) -> Optional[dict]: ...
    @abstractmethod
    def cleanup_jobs(self, cutoff_seconds: int) -> None: ...
    @abstractmethod
    def get_incomplete_jobs(self) -> List[dict]: ...

    # Alert History
    @abstractmethod
    def save_alert_history(self, alert_token: str, username: str, from_status: Optional[str], to_status: str, comment: str) -> int: ...
    @abstractmethod
    def get_alert_history(self, alert_token: str) -> List[dict]: ...

    @abstractmethod
    def list_alerts_for_auto_close(self, hours: int = 48) -> List[dict]: ...

    @abstractmethod
    def get_sla_metrics(self, days: int = 30) -> dict: ...

    # Expected Flux
    @abstractmethod
    def save_expected_flux(self, flux_id: str, division: str, expected_hour: str,
                           source_path: str, active: int = 1) -> None: ...
    @abstractmethod
    def list_expected_flux(self, active_only: bool = False) -> List[dict]: ...
    @abstractmethod
    def update_expected_flux(self, flux_id: str, **kwargs) -> None: ...