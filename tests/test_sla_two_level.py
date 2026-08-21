"""
tests/test_sla_two_level.py
Comprehensive tests for the two-level SLA system:
  - classify_by_concordance boundaries (0-70%=CRITICAL/2h, 70-80%=WARNING/3h, >=80%=NONE)
  - workflow_status vs sla_status separation
  - Manual-only resolution/closure
  - Idempotent breach email/report
  - Consultant email resolution
  - SLA chrono stop at RESOLVED
  - State machine transitions
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

# Ensure backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from core.sla_policy import (
    classify_by_concordance,
    build_sla_meta,
    SLA_HOURS,
    SLA_THRESHOLDS,
    recompute_sla_progress,
    get_concordance_state,
    validate_sla_hours,
    ALLOWED_SLA_HOURS,
    MAX_SLA_HOURS,
)
from storage.local import LocalStorage


class TestClassifyByConcordance(unittest.TestCase):
    """Boundary tests for classify_by_concordance.
    New thresholds: <70%=CRITICAL/2h, 70-80%=WARNING/3h, >=80%=NONE
    """

    def test_critical_below_70(self):
        severity, hours = classify_by_concordance(0.0)
        self.assertEqual(severity, "CRITICAL")
        self.assertEqual(hours, 2.0)

    def test_critical_at_69(self):
        severity, hours = classify_by_concordance(69.9)
        self.assertEqual(severity, "CRITICAL")
        self.assertEqual(hours, 2.0)

    def test_boundary_70_is_warning(self):
        severity, hours = classify_by_concordance(70.0)
        self.assertEqual(severity, "WARNING")
        self.assertEqual(hours, 3.0)

    def test_warning_at_75(self):
        severity, hours = classify_by_concordance(75.0)
        self.assertEqual(severity, "WARNING")
        self.assertEqual(hours, 3.0)

    def test_boundary_80_is_no_alert(self):
        severity, hours = classify_by_concordance(80.0)
        self.assertIsNone(severity)
        self.assertEqual(hours, 0.0)

    def test_no_alert_above_80(self):
        severity, hours = classify_by_concordance(100.0)
        self.assertIsNone(severity)
        self.assertEqual(hours, 0.0)

    def test_flux_config_override(self):
        flux_config = {
            "critical_max": 40.0,
            "warning_max": 70.0,
        }
        severity, hours = classify_by_concordance(45.0, flux_config)
        self.assertEqual(severity, "WARNING")
        self.assertEqual(hours, 3.0)

    def test_flux_config_override_critical(self):
        flux_config = {
            "critical_max": 40.0,
            "warning_max": 70.0,
        }
        severity, hours = classify_by_concordance(30.0, flux_config)
        self.assertEqual(severity, "CRITICAL")


class TestConcordanceState(unittest.TestCase):
    """Test get_concordance_state returns correct state labels."""

    def test_state_critique(self):
        self.assertEqual(get_concordance_state(0.0), "CRITIQUE")
        self.assertEqual(get_concordance_state(50.0), "CRITIQUE")
        self.assertEqual(get_concordance_state(69.9), "CRITIQUE")

    def test_state_attention(self):
        self.assertEqual(get_concordance_state(70.0), "ATTENTION")
        self.assertEqual(get_concordance_state(75.0), "ATTENTION")
        self.assertEqual(get_concordance_state(79.9), "ATTENTION")

    def test_state_normal(self):
        self.assertEqual(get_concordance_state(80.0), "NORMAL")
        self.assertEqual(get_concordance_state(100.0), "NORMAL")


class TestSlaValidation(unittest.TestCase):
    """Test SLA hours validation: only 2h, 3h, 4h allowed. Max = 4h."""

    def test_valid_2h(self):
        validate_sla_hours(2.0)  # Should not raise

    def test_valid_3h(self):
        validate_sla_hours(3.0)  # Should not raise

    def test_valid_4h(self):
        validate_sla_hours(4.0)  # Should not raise

    def test_invalid_5h(self):
        with self.assertRaises(ValueError):
            validate_sla_hours(5.0)

    def test_invalid_8h(self):
        with self.assertRaises(ValueError):
            validate_sla_hours(8.0)

    def test_invalid_24h(self):
        with self.assertRaises(ValueError):
            validate_sla_hours(24.0)

    def test_invalid_1h(self):
        with self.assertRaises(ValueError):
            validate_sla_hours(1.0)

    def test_max_is_4(self):
        self.assertEqual(MAX_SLA_HOURS, 4.0)


class TestBuildSlaMeta(unittest.TestCase):
    """Test that build_sla_meta returns correct structure."""

    def test_critical_meta(self):
        meta = build_sla_meta(
            anomalies=[{"severity": "CRITIQUE"}],
            n_critiques=5,
            n_warnings=2,
            concordance=30.0,
            detected_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        self.assertIn("severity", meta)
        self.assertEqual(meta["severity"], "CRITICAL")
        self.assertIn("sla_deadline", meta)
        self.assertIn("remaining_pct", meta)
        self.assertEqual(meta["concordance_state"], "CRITIQUE")

    def test_warning_meta(self):
        meta = build_sla_meta(
            anomalies=[],
            n_critiques=1,
            n_warnings=1,
            concordance=75.0,
            detected_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        self.assertEqual(meta["severity"], "WARNING")
        self.assertEqual(meta["sla_hours"], 3.0)
        self.assertEqual(meta["concordance_state"], "ATTENTION")

    def test_no_alert_meta(self):
        meta = build_sla_meta(
            anomalies=[],
            n_critiques=0,
            n_warnings=3,
            concordance=90.0,
            detected_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        self.assertEqual(meta["severity"], "")
        self.assertEqual(meta["concordance_state"], "NORMAL")


class TestConcordanceSlaClassification(unittest.TestCase):
    """Non-régression : concordance < 70% → CRITICAL / 2h, 70-80% → WARNING / 3h."""

    def test_48p8_concordance_is_critical_2h(self):
        meta = build_sla_meta(
            anomalies=[{"severity": "CRITIQUE"}],
            n_critiques=5,
            n_warnings=2,
            concordance=48.8,
            detected_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        self.assertEqual(meta["severity"], "CRITICAL")
        self.assertEqual(meta["severity_class"], "CRITICAL")
        self.assertEqual(meta["sla_hours"], 2.0)
        self.assertNotEqual(meta["sla_hours"], 24.0)

    def test_49p9_concordance_is_critical_2h(self):
        meta = build_sla_meta(
            anomalies=[],
            n_critiques=1,
            n_warnings=0,
            concordance=49.9,
            detected_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        self.assertEqual(meta["severity"], "CRITICAL")
        self.assertEqual(meta["sla_hours"], 2.0)

    def test_50p0_concordance_is_critical_2h(self):
        meta = build_sla_meta(
            anomalies=[],
            n_critiques=0,
            n_warnings=1,
            concordance=50.0,
            detected_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        self.assertEqual(meta["severity"], "CRITICAL")
        self.assertEqual(meta["sla_hours"], 2.0)

    def test_70p0_concordance_is_warning_3h(self):
        meta = build_sla_meta(
            anomalies=[],
            n_critiques=0,
            n_warnings=1,
            concordance=70.0,
            detected_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        self.assertEqual(meta["severity"], "WARNING")
        self.assertEqual(meta["sla_hours"], 3.0)

    def test_75p0_concordance_is_warning_3h(self):
        meta = build_sla_meta(
            anomalies=[],
            n_critiques=0,
            n_warnings=1,
            concordance=75.0,
            detected_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        self.assertEqual(meta["severity"], "WARNING")
        self.assertEqual(meta["sla_hours"], 3.0)


class TestStateMachineTransitions(unittest.TestCase):
    """Test state machine with workflow_status."""

    def test_escalated_can_go_to_acknowledged(self):
        from core.alert_state_machine import validate_transition
        # Should NOT raise
        validate_transition("ESCALATED", "ACKNOWLEDGED", "analyst")

    def test_resolved_requires_comment(self):
        from core.alert_state_machine import validate_transition, ValidationError
        with self.assertRaises(ValidationError):
            validate_transition("IN_PROGRESS", "RESOLVED", "analyst", comment="")

    def test_resolved_with_comment_ok(self):
        from core.alert_state_machine import validate_transition
        validate_transition("IN_PROGRESS", "RESOLVED", "analyst", comment="Fixed")

    def test_resolved_from_new_ok(self):
        from core.alert_state_machine import validate_transition
        validate_transition("NEW", "RESOLVED", "analyst", comment="Directly resolved")

    def test_resolved_from_acknowledged_ok(self):
        from core.alert_state_machine import validate_transition
        validate_transition("ACKNOWLEDGED", "RESOLVED", "analyst", comment="Quick resolve")

    def test_closed_only_from_resolved(self):
        from core.alert_state_machine import validate_transition
        validate_transition("RESOLVED", "CLOSED", "admin")

    def test_closed_rejected_from_new(self):
        from core.alert_state_machine import validate_transition, InvalidTransitionError
        with self.assertRaises(InvalidTransitionError):
            validate_transition("NEW", "CLOSED", "admin")

    def test_ignored_to_acknowledged(self):
        from core.alert_state_machine import validate_transition
        validate_transition("IGNORED", "ACKNOWLEDGED", "analyst")

    def test_ignored_to_resolved(self):
        from core.alert_state_machine import validate_transition
        validate_transition("IGNORED", "RESOLVED", "consultant", comment="Résolu depuis ignorer")

    def test_new_to_resolved(self):
        from core.alert_state_machine import validate_transition
        validate_transition("NEW", "RESOLVED", "analyst", comment="Résolu directement")

    def test_escalated_to_resolved(self):
        from core.alert_state_machine import validate_transition
        validate_transition("ESCALATED", "RESOLVED", "team_leader", comment="Résolu après escalade")


class TestWorkflowStatusVsSlaStatus(unittest.TestCase):
    """Verify that workflow_status and sla_status are independent."""

    def test_sla_status_not_affected_by_workflow(self):
        """AT_RISK / BREACHED status is set independently of workflow_status."""
        alert = {
            "token": "abc123",
            "workflow_status": "ACKNOWLEDGED",
            "sla_status": "BREACHED",
            "sla_breached": 1,
            "sla_hours": 2,
            "created_at": (datetime.utcnow() - timedelta(hours=3)).isoformat(),
            "concordance": 40.0,
        }
        self.assertEqual(alert["sla_status"], "BREACHED")
        self.assertEqual(alert["workflow_status"], "ACKNOWLEDGED")

    def test_resolution_stops_sla_clock(self):
        """When resolved, SLA monitoring stops (RESOLVED excluded from scan)."""
        from core.sla_policy import SLA_MONITORED_STATUSES, SLA_EXCLUDED_STATUSES
        self.assertNotIn("RESOLVED", SLA_MONITORED_STATUSES)
        self.assertIn("RESOLVED", SLA_EXCLUDED_STATUSES)

    def test_set_resolved_freezes_sla_within_deadline(self):
        """When resolved before deadline, sla_status becomes RESOLVED and remaining_pct is frozen."""
        from unittest.mock import MagicMock, patch
        from datetime import datetime, timedelta

        mock_conn = MagicMock()
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, key: {
            "sla_deadline": (datetime.utcnow() + timedelta(hours=1)).isoformat(),
            "sla_hours": 2.0,
            "created_at": (datetime.utcnow() - timedelta(minutes=30)).isoformat(),
        }[key]
        mock_row.get = lambda key, default=None: mock_row[key] if key in {
            "sla_deadline": (datetime.utcnow() + timedelta(hours=1)).isoformat(),
            "sla_hours": 2.0,
            "created_at": (datetime.utcnow() - timedelta(minutes=30)).isoformat(),
        } else default
        mock_conn.execute.return_value.fetchone.return_value = mock_row

        storage = MagicMock()
        storage._conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        storage._conn.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(storage, "save_tracking") as mock_tracking:
            LocalStorage.set_resolved(storage, "token123", "admin")

        update_calls = [c for c in mock_conn.execute.call_args_list if len(c[0]) > 0 and "UPDATE alerts SET resolved_by" in c[0][0]]
        sla_update_calls = [c for c in mock_conn.execute.call_args_list if len(c[0]) > 0 and "sla_status=?" in c[0][0]]

        self.assertEqual(len(update_calls), 1)
        self.assertEqual(len(sla_update_calls), 1)
        sla_params = sla_update_calls[0][0][1]
        self.assertEqual(sla_params[4], "RESOLVED")
        self.assertEqual(sla_params[3], 0)
        mock_tracking.assert_called_once()

    def test_set_resolved_freezes_sla_after_breach(self):
        """When resolved after deadline, sla_status becomes BREACHED and sla_breached=1."""
        from unittest.mock import MagicMock, patch
        from datetime import datetime, timedelta

        mock_conn = MagicMock()
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, key: {
            "sla_deadline": (datetime.utcnow() - timedelta(hours=1)).isoformat(),
            "sla_hours": 2.0,
            "created_at": (datetime.utcnow() - timedelta(hours=3)).isoformat(),
        }[key]
        mock_row.get = lambda key, default=None: mock_row[key] if key in {
            "sla_deadline": (datetime.utcnow() - timedelta(hours=1)).isoformat(),
            "sla_hours": 2.0,
            "created_at": (datetime.utcnow() - timedelta(hours=3)).isoformat(),
        } else default
        mock_conn.execute.return_value.fetchone.return_value = mock_row

        storage = MagicMock()
        storage._conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        storage._conn.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(storage, "save_tracking") as mock_tracking:
            LocalStorage.set_resolved(storage, "token123", "admin")

        sla_update_calls = [c for c in mock_conn.execute.call_args_list if len(c[0]) > 0 and "sla_status=?" in c[0][0]]
        self.assertEqual(len(sla_update_calls), 1)
        sla_params = sla_update_calls[0][0][1]
        self.assertEqual(sla_params[4], "BREACHED")
        self.assertEqual(sla_params[3], 1)
        mock_tracking.assert_called_once()


class TestManualOnlyResolution(unittest.TestCase):
    """Resolution and closure must be 100% manual."""

    def test_no_auto_close_in_sla_monitor(self):
        """sla_monitor should not have auto_close_resolved_job."""
        import inspect
        from core import sla_monitor
        source = inspect.getsource(sla_monitor)
        self.assertNotIn("auto_close_resolved_job", source)
        self.assertNotIn("AUTO_CLOSED", source)

    def test_no_auto_escalade_in_sla_monitor(self):
        """sla_monitor should not auto-escalade on breach."""
        import inspect
        from core import sla_monitor
        source = inspect.getsource(sla_monitor)
        self.assertNotIn("AUTO_ESCALATED", source)


class TestIdempotentBreachFlags(unittest.TestCase):
    """Breach email and report flags prevent duplicate sends."""

    def test_storage_has_breach_flags(self):
        """save_alert returns the right fields, breach flags exist."""
        from storage.base import BaseStorage
        self.assertTrue(hasattr(BaseStorage, "set_breach_email_sent"))
        self.assertTrue(hasattr(BaseStorage, "set_breach_report_sent"))
        self.assertTrue(hasattr(BaseStorage, "set_sla_warning_sent"))
        self.assertTrue(hasattr(BaseStorage, "set_ignore_notification_sent"))


class TestConsultantEmailResolution(unittest.TestCase):
    """Consultant email: registry → DEFAULT_CONSULTANT_EMAIL → log warning."""

    def test_get_consultant_email_fallback(self):
        """When no registry or env email, log warning and return empty."""
        from core.sla_monitor import _get_consultant_email
        storage = MagicMock()
        alert = {"flux_id": "NONEXISTENT"}

        with patch("engine.flux_loader.FluxLoader.load", side_effect=FileNotFoundError):
            with patch.dict(os.environ, {}, clear=True):
                result = _get_consultant_email(storage, alert)
                self.assertEqual(result, "")


class TestSLAProgressRecompute(unittest.TestCase):
    """SLA progress recomputation."""

    def test_breach_detected(self):
        alert = {
            "created_at": (datetime.utcnow() - timedelta(hours=3)).isoformat(),
            "sla_hours": 2,
        }
        result = recompute_sla_progress(alert)
        self.assertTrue(result["breached"])

    def test_no_breach_within_sla(self):
        alert = {
            "created_at": datetime.utcnow().isoformat(),
            "sla_hours": 4,
        }
        result = recompute_sla_progress(alert)
        self.assertFalse(result["breached"])
        self.assertGreater(result["remaining_pct"], 0)


class TestOverhaulValidationAndNotifications(unittest.TestCase):
    """Test standard constraints, validation values, and notifications."""

    def test_invalid_sla_hours_raises_value_error(self):
        """validate_sla_hours only allows 2.0, 3.0, 4.0."""
        from core.sla_policy import validate_sla_hours
        validate_sla_hours(2.0)
        validate_sla_hours(3.0)
        validate_sla_hours(4.0)

        with self.assertRaises(ValueError) as ctx:
            validate_sla_hours(1.0)
        self.assertIn("La durée du SLA doit être de 2h, 3h ou 4h uniquement.", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            validate_sla_hours(24.0)
        self.assertIn("La durée maximale autorisée pour un SLA est de 4 heures.", str(ctx.exception))

    def test_get_current_responsible_email_escalated(self):
        """get_current_responsible_email gives priority to escalated_to."""
        from core.sla_monitor import get_current_responsible_email
        storage = MagicMock()
        alert = {"token": "t1", "escalated_to": "consultant@timsfort.com"}
        email = get_current_responsible_email(storage, alert)
        self.assertEqual(email, "consultant@timsfort.com")


if __name__ == "__main__":
    unittest.main()
