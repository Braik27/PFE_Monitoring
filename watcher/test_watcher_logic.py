# -*- coding: utf-8 -*-
"""
watcher/test_watcher_logic.py
Tests unitaires pour valider la logique décisionnelle du watcher (sans dépendances I/O ou DB).
"""

import datetime
from watcher import should_alert


def test_should_alert_before_expected_hour():
    """
    Cas 1 : L'heure courante (12:00) est antérieure à l'heure attendue (18:00).
    Aucune alerte ne doit être déclenchée.
    """
    now = datetime.datetime(2026, 7, 15, 12, 0, 0)
    expected_hour_str = "18:00"
    last_check_at_str = None
    last_status = None
    files_exist = False

    result = should_alert(now, expected_hour_str, last_check_at_str, last_status, files_exist)
    assert result is False


def test_should_alert_after_expected_hour_no_files_no_check():
    """
    Cas 2 : L'heure courante (19:00) a dépassé l'heure attendue (18:00), 
    les fichiers ne sont pas là et aucun traitement n'a eu lieu aujourd'hui.
    Une alerte DOIT être déclenchée.
    """
    now = datetime.datetime(2026, 7, 15, 19, 0, 0)
    expected_hour_str = "18:00"
    last_check_at_str = None
    last_status = None
    files_exist = False

    result = should_alert(now, expected_hour_str, last_check_at_str, last_status, files_exist)
    assert result is True


def test_should_not_alert_if_files_are_present():
    """
    Cas 3 : L'heure courante (19:00) a dépassé l'heure attendue (18:00) mais
    les fichiers sont présents dans le dossier (ils vont être traités).
    Aucune alerte ne doit être déclenchée.
    """
    now = datetime.datetime(2026, 7, 15, 19, 0, 0)
    expected_hour_str = "18:00"
    last_check_at_str = None
    last_status = None
    files_exist = True

    result = should_alert(now, expected_hour_str, last_check_at_str, last_status, files_exist)
    assert result is False


def test_should_not_alert_if_already_processed_today():
    """
    Cas 4 : L'heure est dépassée, mais le traitement a déjà réussi aujourd'hui.
    Aucune alerte ne doit être déclenchée.
    """
    now = datetime.datetime(2026, 7, 15, 19, 0, 0)
    expected_hour_str = "18:00"
    last_check_at_str = "2026-07-15T18:15:00"
    last_status = "SUCCESS"
    files_exist = False

    result = should_alert(now, expected_hour_str, last_check_at_str, last_status, files_exist)
    assert result is False


def test_should_not_alert_if_already_alerted_today():
    """
    Cas 5 : L'heure est dépassée, les fichiers manquent, mais une alerte a déjà été
    créée aujourd'hui. On ne doit pas spammer ou re-déclencher de nouvelles alertes.
    """
    now = datetime.datetime(2026, 7, 15, 19, 0, 0)
    expected_hour_str = "18:00"
    last_check_at_str = "2026-07-15T18:01:00"
    last_status = "MISSING"
    files_exist = False

    result = should_alert(now, expected_hour_str, last_check_at_str, last_status, files_exist)
    assert result is False


def test_should_alert_if_processed_yesterday_but_missing_today():
    """
    Cas 6 : Hier tout s'est bien passé, mais aujourd'hui (19:00) on a passé l'heure attendue
    (18:00) et les fichiers ne sont pas là.
    Une alerte DOIT être déclenchée pour aujourd'hui.
    """
    now = datetime.datetime(2026, 7, 15, 19, 0, 0)
    expected_hour_str = "18:00"
    # Dernier traitement hier (14 juillet)
    last_check_at_str = "2026-07-14T18:10:00"
    last_status = "SUCCESS"
    files_exist = False

    result = should_alert(now, expected_hour_str, last_check_at_str, last_status, files_exist)
    assert result is True


def test_should_not_alert_on_invalid_time_format():
    """
    Cas 7 : Le format d'heure limite est invalide dans la configuration.
    L'alerte doit être désactivée pour éviter des faux positifs.
    """
    now = datetime.datetime(2026, 7, 15, 19, 0, 0)
    expected_hour_str = "invalid_format"
    last_check_at_str = None
    last_status = None
    files_exist = False

    result = should_alert(now, expected_hour_str, last_check_at_str, last_status, files_exist)
    assert result is False
