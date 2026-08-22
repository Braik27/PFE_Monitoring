"""
core/email_service.py — Mécanisme d'envoi SMTP unique (source de vérité).

Le CONTENU des emails (sujets, templates HTML, sélection des destinataires,
gating métier type ALERT_EMAIL_ENABLED) reste dans les modules appelants.
Ce module unifie uniquement :
  - la lecture de la configuration ALERT_SMTP_* (une seule implémentation)
  - la connexion/authentification/envoi SMTP avec STARTTLS et un contexte SSL
    explicite (ssl.create_default_context — vérification du certificat, anti-MITM)

Toutes les adresses/définitions de message restent propres à chaque appelant ;
seul le mécanisme d'envoi est partagé.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Iterable, Optional, Union

log = logging.getLogger(__name__)

DEFAULT_FROM_FALLBACK = "noreply@fluxmonitor.timsoft.com"

Recipients = Union[str, Iterable[str]]


def get_smtp_config() -> dict:
    """Configuration SMTP partagée. Host vide = SMTP non configuré (fail-closed)."""
    user = os.environ.get("ALERT_SMTP_USER", "")
    return {
        "host": os.environ.get("ALERT_SMTP_HOST", ""),
        "port": int(os.environ.get("ALERT_SMTP_PORT", "587")),
        "user": user,
        "password": os.environ.get("ALERT_SMTP_PASSWORD", ""),
        "from_addr": (
            os.environ.get("ALERT_EMAIL_FROM", "")
            or user
            or DEFAULT_FROM_FALLBACK
        ),
    }


def send_email(
    to_addrs: Recipients,
    subject: str,
    body_html: str,
    *,
    from_addr: Optional[str] = None,
    smtp_host: Optional[str] = None,
    smtp_port: Optional[int] = None,
    smtp_user: Optional[str] = None,
    smtp_password: Optional[str] = None,
    timeout: int = 15,
) -> bool:
    """
    Envoi SMTP unique de tous les emails applicatifs.

    - to_addrs : adresse simple ou toute iterable d'adresses
    - les paramètres explicites surchargent la configuration ALERT_SMTP_*
    - ne lève jamais d'exception ; retourne True si envoyé, False sinon
      (non configuré, destinataire manquant ou erreur SMTP)
    """
    if isinstance(to_addrs, str):
        recipients = [to_addrs]
    else:
        recipients = [a for a in (to_addrs or []) if a]
    if not recipients:
        log.warning("[EMAIL] Destinataire manquant — envoi annulé")
        return False

    cfg = get_smtp_config()
    host = smtp_host if smtp_host is not None else cfg["host"]
    port = int(smtp_port) if smtp_port is not None else cfg["port"]
    user = smtp_user if smtp_user is not None else cfg["user"]
    password = smtp_password if smtp_password is not None else cfg["password"]
    sender = from_addr or cfg["from_addr"]

    if not host or not user:
        log.warning("[EMAIL] SMTP non configuré (ALERT_SMTP_HOST/USER) — envoi vers %s annulé", recipients[0])
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(host, port, timeout=timeout) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.login(user, password)
            server.sendmail(sender, recipients, msg.as_string())
        log.info("[EMAIL] Envoyé → %s", recipients[0])
        return True
    except Exception as exc:
        log.error("[EMAIL] Échec envoi → %s : %s", recipients[0], exc)
        return False
