"""Gestion du stockage local et en ligne des rapports JSON complets d'analyses."""
import json
import gzip
import logging
import os
from datetime import datetime, timezone
from config import settings

log = logging.getLogger(__name__)


def get_reports_dir() -> str:
    """Retourne le répertoire local persistant pour le stockage des rapports."""
    db_path = os.path.abspath(str(settings.local.DB_PATH))
    db_dir = os.path.dirname(db_path)
    reports_dir = os.path.join(db_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    return reports_dir


def upload_report_to_blob(summary: dict, flux_id: str) -> str | None:
    """
    Sauvegarde le rapport d'analyse.
    Si STORAGE_BACKEND=local ou si la connexion Azure échoue, il est stocké localement.
    Retourne le chemin relatif (avec préfixe local:// si local).
    """
    try:
        raw_payload = json.dumps(summary, ensure_ascii=False, default=str).encode("utf-8")
        compressed = gzip.compress(raw_payload, compresslevel=6)
    except Exception as e:
        log.exception("[REPORT_STORE] Échec de la sérialisation du rapport : %s", e)
        return None

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{flux_id}/{ts}.json.gz"

    use_local = (settings.BACKEND == "local")
    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")

    # Si configuré sur Azure et que la chaîne de connexion est fournie
    if not use_local and conn_str:
        try:
            from azure.storage.blob import BlobServiceClient, ContentSettings
            client = BlobServiceClient.from_connection_string(conn_str)
            container = client.get_container_client("flux-results")
            container.upload_blob(
                name=filename,
                data=compressed,
                overwrite=True,
                content_settings=ContentSettings(
                    content_type="application/json",
                    content_encoding="gzip",
                ),
                connection_timeout=60,
            )
            log.info("[REPORT_STORE] Rapport téléversé avec succès sur Azure Blob : %s", filename)
            return filename
        except Exception as e:
            log.warning("[REPORT_STORE] Échec de l'upload Azure, repli vers le stockage local : %s", e)

    # Sauvegarde locale (fallback ou mode local explicite)
    try:
        reports_dir = get_reports_dir()
        local_path = os.path.join(reports_dir, flux_id, f"{ts}.json.gz")
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(compressed)
        log.info("[REPORT_STORE] Rapport sauvegardé localement : %s", local_path)
        return f"local://{filename}"
    except Exception as e:
        log.exception("[REPORT_STORE] Échec de la sauvegarde locale : %s", e)
        return None


def download_report(blob_path: str) -> dict | None:
    """
    Charge le rapport d'analyse à partir de son chemin (local ou Azure Blob).
    """
    if not blob_path:
        return None

    is_local_path = blob_path.startswith("local://")
    clean_path = blob_path.replace("local://", "")

    # 1. Si le chemin est explicitement local, on le lit localement
    if is_local_path:
        reports_dir = get_reports_dir()
        local_filepath = os.path.join(reports_dir, clean_path)
        if os.path.exists(local_filepath):
            try:
                with open(local_filepath, "rb") as f:
                    data = f.read()
                return json.loads(gzip.decompress(data).decode("utf-8"))
            except Exception as e:
                log.exception("[REPORT_STORE] Échec de lecture du rapport local : %s", e)
                return None
        else:
            log.error("[REPORT_STORE] Fichier de rapport local introuvable : %s", local_filepath)
            return None

    # 2. Sinon, on tente de le lire depuis Azure si configuré
    use_azure = (settings.BACKEND == "azure")
    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING") if use_azure else None
    if conn_str:
        try:
            from azure.storage.blob import BlobServiceClient
            client = BlobServiceClient.from_connection_string(conn_str)
            container = client.get_container_client("flux-results")
            blob_data = container.download_blob(clean_path).readall()
            try:
                return json.loads(blob_data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return json.loads(gzip.decompress(blob_data).decode("utf-8"))
        except Exception as e:
            log.warning("[REPORT_STORE] Échec du téléchargement Azure pour %s : %s. Tentative de repli local...", clean_path, e)

    # 3. Repli local : recherche dans le dossier reports local même si le chemin n'a pas le préfixe local://
    reports_dir = get_reports_dir()
    local_filepath = os.path.join(reports_dir, clean_path)
    if os.path.exists(local_filepath):
        try:
            with open(local_filepath, "rb") as f:
                data = f.read()
            log.info("[REPORT_STORE] Rapport chargé depuis le repli local : %s", local_filepath)
            return json.loads(gzip.decompress(data).decode("utf-8"))
        except Exception as e:
            log.exception("[REPORT_STORE] Échec de lecture du fichier de repli local : %s", e)

    return None
