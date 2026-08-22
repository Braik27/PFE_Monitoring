"""
migrate_stats_columns.py — À lancer UNE SEULE FOIS après avoir appliqué
les patches sur local.py / azure_backend.py.

Remplit les nouvelles colonnes (total_critiques, total_warnings,
concordance_moyenne) pour toutes les analyses déjà existantes, en
relisant leur summary JSON.

Usage :
    cd backend
    python migrate_stats_columns.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from storage import get_storage


def migrate():
    db = get_storage()
    rows = db.list_analyses(limit=10000)
    print(f"{len(rows)} analyses trouvées. Migration en cours...\n")

    updated = 0
    for r in rows:
        s = r.get("summary", {}) or {}
        total_crit = s.get("total_critiques", 0)
        total_warn = s.get("total_warnings", 0)
        conc_moy   = s.get("concordance_moyenne", 100.0)

        with db._conn() as conn:
            if hasattr(conn, "execute"):  # SQLite (LocalStorage)
                conn.execute(
                    "UPDATE analyses SET total_critiques=?, total_warnings=?, "
                    "concordance_moyenne=? WHERE id=?",
                    (total_crit, total_warn, conc_moy, r["id"])
                )
            else:  # pyodbc (AzureStorage)
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE analyses SET total_critiques=?, total_warnings=?, "
                    "concordance_moyenne=? WHERE id=?",
                    (total_crit, total_warn, conc_moy, r["id"])
                )
                conn.commit()

        updated += 1
        print(f"  ✅ analyse {r['id']} ({r.get('flux_id')}): "
              f"crit={total_crit}, warn={total_warn}, conc={conc_moy}")

    print(f"\n🎉 Migration terminée : {updated} analyses mises à jour.")


if __name__ == "__main__":
    migrate()