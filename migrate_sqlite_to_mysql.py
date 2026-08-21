# -*- coding: utf-8 -*-
"""
migrate_sqlite_to_mysql.py
Script de migration des données existantes de la base SQLite vers la base MySQL.
Conserve le fichier .db original.
"""

import os
import sys
import sqlite3
from sqlalchemy import create_engine, text

# Ajout du dossier backend au path pour importer la config et la couche de stockage
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(CURRENT_DIR, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.append(BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

from storage import get_storage


def migrate():
    sqlite_db_path = os.path.join(BACKEND_DIR, "instance", "flux_monitor.db")
    if not os.path.exists(sqlite_db_path):
        print(f"❌ Base SQLite introuvable à : {sqlite_db_path}")
        return

    print(f"📂 Base SQLite trouvée à : {sqlite_db_path}")
    print("🔌 Initialisation de la base MySQL...")
    
    # get_storage() appelle automatiquement init_db() qui va créer
    # les schémas de table si elles n'existent pas encore dans MySQL.
    storage = get_storage()
    mysql_engine = storage._engine

    # Connexion à la base SQLite source
    sqlite_conn = sqlite3.connect(sqlite_db_path)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cursor = sqlite_conn.cursor()

    # Liste ordonnée des tables à migrer
    tables = [
        "users",
        "divisions",
        "expected_flux",
        "smart_mappings",
        "conversations",
        "messages",
        "user_patterns",
        "alerts",
        "alert_tracking",
        "alert_history",
        "ia_feedbacks",
        "correction_history",
        "analyses",
        "ecarts",
        "jobs"
    ]

    with mysql_engine.connect() as mysql_conn:
        # Démarrer une transaction globale pour la migration
        trans = mysql_conn.begin()
        try:
            # Désactiver temporairement les contraintes de clés étrangères pour éviter les conflits d'insertion
            mysql_conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
            
            for table in tables:
                print(f"➡️ Table {table} : migration en cours...")
                
                # Vérifier si la table existe dans SQLite
                sqlite_cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
                if not sqlite_cursor.fetchone():
                    print(f"  ⚠️ Table {table} absente dans SQLite, ignorée.")
                    continue

                # Récupérer les lignes depuis SQLite
                sqlite_cursor.execute(f"SELECT * FROM `{table}`")
                rows = sqlite_cursor.fetchall()
                if not rows:
                    print(f"  ℹ️ Table {table} vide. Rien à migrer.")
                    continue

                # Vider la table cible dans MySQL
                mysql_conn.execute(text(f"TRUNCATE TABLE `{table}`"))

                # Construire la requête d'insertion en utilisant les paramètres nommés SQLAlchemy
                cols = list(rows[0].keys())
                col_names = ", ".join([f"`{c}`" for c in cols])
                placeholders = ", ".join([f":{c}" for c in cols])
                insert_sql = f"INSERT INTO `{table}` ({col_names}) VALUES ({placeholders})"

                # Convertir les lignes en dictionnaires
                data = [dict(row) for row in rows]

                # Insertion ligne par ligne pour éviter de dépasser max_allowed_packet de MySQL
                for item in data:
                    mysql_conn.execute(text(insert_sql), item)
                print(f"  ✅ {len(rows)} lignes migrées avec succès.")
            
            # Valider la transaction
            trans.commit()
            print("\n🎉 Migration de SQLite vers MySQL terminée avec succès !")

        except Exception as e:
            # Annuler la transaction en cas d'erreur
            trans.rollback()
            print(f"\n❌ Erreur pendant la migration : {e}")
            raise
        finally:
            try:
                mysql_conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))
            except Exception:
                pass
            sqlite_conn.close()


if __name__ == "__main__":
    migrate()
