# -*- coding: utf-8 -*-
"""
watcher/watcher.py
Script externe pour surveiller les dossiers d'import et déclencher
les analyses automatiques ou lever des alertes si les fichiers attendus manquent.
"""

import os
import sys
import time
import shutil
import datetime
import logging
import threading
import requests
from dotenv import load_dotenv

# Ajout du dossier backend au sys.path pour importer la couche de stockage existante
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(CURRENT_DIR, "..", "backend")
if BACKEND_DIR not in sys.path:
    sys.path.append(BACKEND_DIR)

from storage import get_storage
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import schedule

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(CURRENT_DIR, "watcher.log"), encoding="utf-8")
    ]
)
log = logging.getLogger("watcher")

# Chargement des variables d'environnement
load_dotenv(os.path.join(BACKEND_DIR, ".env"))
load_dotenv(os.path.join(CURRENT_DIR, ".env"))

API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")
WATCHER_DIR = os.path.abspath(os.environ.get("WATCHER_DIR", os.path.join(CURRENT_DIR, "watch_folder")))
WATCHER_USER = os.environ.get("WATCHER_USER", "watcher_agent")
WATCHER_PASSWORD = os.environ.get("WATCHER_PASSWORD", "watcher_pass_123")

log.info("Dossier surveillé : %s", WATCHER_DIR)
log.info("API URL de destination : %s", API_BASE_URL)


def should_alert(now: datetime.datetime, expected_hour_str: str,
                 last_check_at_str: str, last_status: str,
                 files_exist: bool) -> bool:
    """
    Détermine si une alerte de fichier manquant doit être déclenchée.
    Logique pure séparée de l'I/O pour être testable unitairement.
    
    Retourne True si l'heure limite est dépassée, qu'aucune exécution ou alerte
    n'a été enregistrée aujourd'hui, et que les fichiers physiques n'existent pas.
    """
    try:
        exp_h, exp_m = map(int, expected_hour_str.split(":"))
        expected_time = now.replace(hour=exp_h, minute=exp_m, second=0, microsecond=0)
    except Exception:
        # Heure mal configurée, on ne déclenche pas d'alerte pour ne pas polluer
        return False

    if now < expected_time:
        return False

    today_str = now.strftime("%Y-%m-%d")

    # Déjà traité aujourd'hui avec succès ?
    if last_check_at_str and last_status in ("SUCCESS", "PROCESSED"):
        if last_check_at_str.startswith(today_str):
            return False

    # Alerte déjà générée aujourd'hui ?
    if last_check_at_str and last_status in ("MISSING", "ALERTE_CREEE"):
        if last_check_at_str.startswith(today_str):
            return False

    # Si les fichiers existent physiquement dans le dossier, on n'alerte pas
    # car ils vont être traités sous peu par le watchdog.
    if files_exist:
        return False

    return True


def trigger_comparison(flux_id: str, division: str, cegid_path: str, oracle_path: str) -> bool:
    """
    S'authentifie auprès de l'API Flask et envoie une requête POST
    pour lancer la comparaison entre les deux fichiers CSV détectés.
    """
    session = requests.Session()
    login_url = f"{API_BASE_URL}/api/login"
    comparer_url = f"{API_BASE_URL}/api/flux/comparer"

    try:
        # Authentification via le compte technique dédié
        log.info("Connexion technique avec le compte : %s", WATCHER_USER)
        login_resp = session.post(login_url, json={
            "username": WATCHER_USER,
            "password": WATCHER_PASSWORD
        }, timeout=10)

        if login_resp.status_code != 200:
            log.error("Échec de la connexion à l'API : %s (Status: %d)", login_resp.text, login_resp.status_code)
            return False

        # Envoi des fichiers en multipart/form-data
        log.info("Envoi des fichiers à l'API pour comparaison automatique (flux: %s)...", flux_id)
        with open(cegid_path, "rb") as fc, open(oracle_path, "rb") as fo:
            files = {
                "cegid": ("cegid.csv", fc, "text/csv"),
                "oracle": ("oracle.csv", fo, "text/csv")
            }
            data = {
                "flux_id": flux_id,
                "division": division,
                "analyst": WATCHER_USER
            }
            resp = session.post(comparer_url, data=data, files=files, timeout=30)

        if resp.status_code in (200, 201, 202):
            job_id = resp.json().get("job_id", "inconnu")
            log.info("✅ Comparaison lancée avec succès pour %s. Job ID: %s", flux_id, job_id)
            
            # Enregistrement du succès dans la base de données
            try:
                db = get_storage()
                db.update_expected_flux(
                    flux_id=flux_id,
                    last_check_at=datetime.datetime.now().isoformat(),
                    last_status="SUCCESS"
                )
            except Exception as db_err:
                log.warning("Impossible de mettre à jour le statut du flux en BDD : %s", db_err)
            return True
        else:
            log.error("❌ Échec du déclenchement de la comparaison pour %s : %s", flux_id, resp.text)
            return False

    except Exception as e:
        log.error("❌ Erreur de communication réseau lors du déclenchement : %s", e)
        return False


def trigger_missing_file_alert(flux_id: str, division: str, expected_hour: str) -> bool:
    """
    S'authentifie et envoie une requête POST à l'API Flask
    pour créer une alerte critique FICHIER_MANQUANT pour ce flux.
    """
    session = requests.Session()
    login_url = f"{API_BASE_URL}/api/login"
    alert_url = f"{API_BASE_URL}/api/alerts/manual"

    try:
        # Authentification
        login_resp = session.post(login_url, json={
            "username": WATCHER_USER,
            "password": WATCHER_PASSWORD
        }, timeout=10)

        if login_resp.status_code != 200:
            log.error("Échec connexion watcher pour alerte de fichier manquant : %s", login_resp.text)
            return False

        # Envoi de l'alerte
        payload = {
            "flux_id": flux_id,
            "division": division,
            "expected_hour": expected_hour,
            "label": "Fichier attendu manquant",
            "flux_name": f"Flux {flux_id} ({division})"
        }
        resp = session.post(alert_url, json=payload, timeout=10)

        if resp.status_code in (200, 201):
            log.info("🚨 Alerte FICHIER_MANQUANT créée avec succès pour le flux %s", flux_id)
            return True
        else:
            log.error("❌ Échec de la création de l'alerte pour %s : %s", flux_id, resp.text)
            return False

    except Exception as e:
        log.error("❌ Erreur de réseau lors de la création de l'alerte pour %s : %s", flux_id, e)
        return False


class FluxHandler(FileSystemEventHandler):
    """
    Handler Watchdog pour surveiller la présence conjointe de cegid.csv et oracle.csv
    dans les sous-dossiers.
    """
    def __init__(self, watcher_dir):
        super().__init__()
        self.watcher_dir = os.path.abspath(watcher_dir)
        self.lock = threading.Lock()

    def on_any_event(self, event):
        if event.is_directory:
            return

        filepath = os.path.abspath(event.src_path)
        relpath = os.path.relpath(filepath, self.watcher_dir)
        parts = relpath.split(os.sep)

        # Les fichiers doivent être dans un sous-dossier direct : WATCHER_DIR/FLUX_ID/fichier.csv
        if len(parts) < 2:
            return

        subfolder = parts[0]
        # On ignore les dossiers de traitement 'processed' ou les fichiers cachés
        if subfolder.startswith('.') or subfolder.lower() == 'processed':
            return

        filename = parts[-1].lower()
        if filename in ("cegid.csv", "oracle.csv"):
            self.check_and_trigger(subfolder)

    def check_and_trigger(self, subfolder):
        with self.lock:
            subfolder_path = os.path.join(self.watcher_dir, subfolder)
            cegid_path = os.path.join(subfolder_path, "cegid.csv")
            oracle_path = os.path.join(subfolder_path, "oracle.csv")

            if os.path.exists(cegid_path) and os.path.exists(oracle_path):
                # Pause pour s'assurer que les fichiers ont fini d'être écrits
                try:
                    size_c = os.path.getsize(cegid_path)
                    size_o = os.path.getsize(oracle_path)
                    time.sleep(1.5)
                    if os.path.getsize(cegid_path) != size_c or os.path.getsize(oracle_path) != size_o:
                        log.debug("Fichiers en cours d'écriture dans %s, report...", subfolder)
                        return
                except OSError:
                    return

                # Empêcher le traitement de fichiers vides
                if os.path.getsize(cegid_path) == 0 or os.path.getsize(oracle_path) == 0:
                    log.warning("Un des fichiers détectés dans %s est vide. En attente de contenu.", subfolder)
                    return

                log.info("Couple complet détecté dans le sous-dossier : %s", subfolder)

                # Création du sous-dossier d'archive horodaté
                processed_dir = os.path.join(subfolder_path, "processed")
                os.makedirs(processed_dir, exist_ok=True)

                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                target_cegid = os.path.join(processed_dir, f"{timestamp}_cegid.csv")
                target_oracle = os.path.join(processed_dir, f"{timestamp}_oracle.csv")

                # Déplacement immédiat pour éviter de re-déclencher des événements watchdog
                try:
                    shutil.move(cegid_path, target_cegid)
                    shutil.move(oracle_path, target_oracle)
                except Exception as move_err:
                    log.error("Impossible de déplacer les fichiers vers processed : %s", move_err)
                    return

                # Lancement de la comparaison
                # division et flux_id correspondent par défaut au nom du sous-dossier
                trigger_comparison(
                    flux_id=subfolder,
                    division=subfolder,
                    cegid_path=target_cegid,
                    oracle_path=target_oracle
                )


def check_missing_files_job():
    """
    Parcourt la table expected_flux pour lever des alertes si les fichiers
    attendus ne sont pas arrivés avant l'heure limite.
    """
    log.info("Vérification planifiée des flux attendus...")
    try:
        db = get_storage()
        flux_list = db.list_expected_flux(active_only=True)
    except Exception as e:
        log.error("Impossible d'accéder à la base de données pour lister les flux attendus : %s", e)
        return

    now = datetime.datetime.now()
    
    for flux in flux_list:
        flux_id = flux["flux_id"]
        expected_hour_str = flux["expected_hour"]
        division = flux["division"]
        source_path = flux["source_path"]
        last_check_at = flux.get("last_check_at")
        last_status = flux.get("last_status")

        # Vérification de l'existence physique actuelle de fichiers prêts à être traités
        subfolder_path = os.path.join(WATCHER_DIR, source_path)
        cegid_file = os.path.join(subfolder_path, "cegid.csv")
        oracle_file = os.path.join(subfolder_path, "oracle.csv")
        files_exist = os.path.exists(cegid_file) and os.path.exists(oracle_file)

        # Calcul décisionnel via la fonction pure
        if should_alert(now, expected_hour_str, last_check_at, last_status, files_exist):
            log.warning("Flux attendu manquant pour %s (heure limite : %s)", flux_id, expected_hour_str)
            
            # Déclenchement de l'alerte
            success = trigger_missing_file_alert(flux_id, division, expected_hour_str)
            if success:
                try:
                    db.update_expected_flux(
                        flux_id=flux_id,
                        last_check_at=now.isoformat(),
                        last_status="MISSING"
                    )
                except Exception as db_err:
                    log.warning("Impossible de mettre à jour le statut du flux en BDD après alerte : %s", db_err)


def run_scheduler():
    """Boucle infinie du planificateur (exécutée dans un thread séparé)."""
    while True:
        schedule.run_pending()
        time.sleep(1)


def main():
    os.makedirs(WATCHER_DIR, exist_ok=True)
    
    # Configuration de la vérification planifiée toutes les minutes en local
    schedule.every(1).minutes.do(check_missing_files_job)
    log.info("Planificateur configuré pour tourner toutes les minutes.")

    # Lancement du planificateur dans un thread démon
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()

    # Démarrage de watchdog
    event_handler = FluxHandler(WATCHER_DIR)
    observer = Observer()
    observer.schedule(event_handler, WATCHER_DIR, recursive=True)
    observer.start()
    log.info("Observateur de fichiers démarré sur : %s", WATCHER_DIR)

    # Exécution immédiate au démarrage pour combler les manques
    check_missing_files_job()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Arrêt de l'observateur...")
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
