"""
storage/azure_backend.py — Azure SQL + Blob Storage
"""

from __future__ import annotations
import json
import os
import logging
from typing import List, Optional
from azure.storage.blob import BlobServiceClient
import pyodbc
from storage.base import BaseStorage, json_encode
from config import settings

log = logging.getLogger(__name__)


class AzureStorage(BaseStorage):
    def __init__(self):
        self.sql_conn_str = settings.azure.SQL_CONNECTION_STRING
        self.blob_conn_str = settings.azure.BLOB_CONNECTION_STRING
        self.container_cegid = settings.azure.BLOB_CONTAINER_CEGID
        self.container_oracle = settings.azure.BLOB_CONTAINER_ORACLE
        self.container_results = settings.azure.BLOB_CONTAINER_RESULTS
        self._blob_service = None

        if not self.sql_conn_str:
            raise EnvironmentError(
                "AZURE_SQL_CONNECTION_STRING n'est pas définie. "
                "Vérifiez vos variables d'environnement Azure App Settings."
            )
        if not self.blob_conn_str:
            log.warning("AZURE_STORAGE_CONNECTION_STRING non définie - le stockage de fichiers ne fonctionnera pas.")

    @property
    def blob_service(self) -> BlobServiceClient:
        if self._blob_service is None:
            if not self.blob_conn_str:
                raise RuntimeError("La connexion Azure Blob n'est pas configurée (AZURE_STORAGE_CONNECTION_STRING manquante)")
            self._blob_service = BlobServiceClient.from_connection_string(self.blob_conn_str)
        return self._blob_service

    def _conn(self):
        try:
            return pyodbc.connect(self.sql_conn_str, timeout=30)
        except pyodbc.Error as e:
            log.error("Erreur de connexion Azure SQL: %s", e)
            raise ConnectionError(f"Impossible de se connecter à Azure SQL: {str(e)}")

    def init_db(self):
        try:
            with self._conn() as conn:
                cursor = conn.cursor()

                # Divisions seed
                cursor.execute("SELECT COUNT(*) FROM divisions")
                if cursor.fetchone()[0] == 0:
                    divisions_seed = [
                        ("DOHA",  "ABA Luxury Doha",                    "QA", "🇶🇦"),
                        ("KWT",   "ABA WATCHES AND JEWELRY Luxury Kuwait","KW", "🇰🇼"),
                        ("SPG",   "Sports Gate Technogym (PSG)",         "QA", "🇶🇦"),
                        ("KSA",   "Platinum Sand KSA (PSC KSA)",         "SA", "🇸🇦"),
                    ]
                    for code, name, country, flag in divisions_seed:
                        cursor.execute(
                            "INSERT INTO divisions (code, name, country, flag, active) VALUES (?,?,?,?,1)",
                            (code, name, country, flag)
                        )
                    conn.commit()

                    log.info("Tables Azure SQL initialisées avec succès (données seed vérifiées).")
                    conn.commit()

                cursor.execute("""
                    IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[expected_flux]') AND type in (N'U'))
                    BEGIN
                        CREATE TABLE [dbo].[expected_flux] (
                            [flux_id] VARCHAR(100) NOT NULL PRIMARY KEY,
                            [division] VARCHAR(50) NOT NULL,
                            [expected_hour] VARCHAR(10) NOT NULL,
                            [source_path] VARCHAR(500) NOT NULL,
                            [active] INT NOT NULL DEFAULT 1,
                            [last_check_at] VARCHAR(100) NULL,
                            [last_status] VARCHAR(100) NULL
                        );
                    END
                """)
                conn.commit()

                cursor.execute("""
                    IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'idx_analyses_flux_date' AND object_id = OBJECT_ID('analyses'))
                    CREATE INDEX idx_analyses_flux_date ON analyses (flux_id, created_at DESC)
                """)
                conn.commit()
        except Exception as e:
            log.error("Erreur lors de l'initialisation de la base de données Azure SQL: %s", e)
            raise RuntimeError(f"Échec de l'initialisation de la base de données: {str(e)}")

    def _row_to_dict(self, cursor, row) -> dict:
        if not row:
            return {}
        columns = [col[0] for col in cursor.description]
        d = dict(zip(columns, row))
        # ISO formats
        for k, v in list(d.items()):
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat()
        return d

    # ── Analyses ──────────────────────────────────────────────────────────────

    def save_analysis(self, flux_id, label, summary) -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO analyses (flux_id, label, summary) VALUES (?,?,?)",
                (flux_id, label, json_encode(summary))
            )
            conn.commit()
            cursor.execute("SELECT @@IDENTITY AS id")
            return int(cursor.fetchone()[0])

    def get_analysis(self, analysis_id):
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM analyses WHERE id=?", (analysis_id,))
            row = cursor.fetchone()
            if not row:
                return None
            d = self._row_to_dict(cursor, row)
            if d.get("summary"):
                try:
                    d["summary"] = json.loads(d["summary"])
                except Exception:
                    d["summary"] = {}
            return d

    def list_analyses(self, flux_id=None, limit=50) -> List[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            if flux_id:
                cursor.execute(
                    "SELECT * FROM analyses WHERE flux_id=? ORDER BY created_at DESC OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY",
                    (flux_id, limit)
                )
            else:
                cursor.execute(
                    "SELECT * FROM analyses ORDER BY created_at DESC OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY",
                    (limit,)
                )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]
            result = []
            for row in rows:
                d = dict(zip(columns, row))
                if hasattr(d.get("created_at"), "isoformat"):
                    d["created_at"] = d["created_at"].isoformat()
                if d.get("summary"):
                    try:
                        d["summary"] = json.loads(d["summary"])
                    except Exception:
                        d["summary"] = {}
                result.append(d)
            return result

    def count_analyses(self, flux_id=None) -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            if flux_id:
                cursor.execute("SELECT COUNT(*) FROM analyses WHERE flux_id=?", (flux_id,))
            else:
                cursor.execute("SELECT COUNT(*) FROM analyses")
            row = cursor.fetchone()
        return row[0] if row else 0

    def count_analyses_by_analyst(self, username: str) -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            term1 = f'%"analyst":"{username}"%'
            term2 = f'%"analyst": "{username}"%'
            cursor.execute(
                "SELECT COUNT(*) FROM analyses WHERE summary LIKE ? OR summary LIKE ?",
                (term1, term2)
            )
            row = cursor.fetchone()
        return row[0] if row else 0

    def delete_analysis(self, analysis_id):
        with self._conn() as conn:
            conn.cursor().execute("DELETE FROM analyses WHERE id=?", (analysis_id,))
            conn.commit()

    def update_summary(self, analysis_id, summary):
        with self._conn() as conn:
            conn.cursor().execute(
                "UPDATE analyses SET summary=? WHERE id=?",
                (json_encode(summary), analysis_id)
            )
            conn.commit()

    # ── Users ─────────────────────────────────────────────────────────────────

    def save_user(self, username, password_hash, role="analyst") -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                MERGE INTO users AS target
                USING (VALUES (?, ?, ?)) AS source (username, password_hash, role)
                ON target.username = source.username
                WHEN MATCHED THEN
                    UPDATE SET password_hash = source.password_hash, role = source.role
                WHEN NOT MATCHED THEN
                    INSERT (username, password_hash, role, active)
                    VALUES (source.username, source.password_hash, source.role, 1);
            """, (username, password_hash, role))
            conn.commit()
            cursor.execute("SELECT id FROM users WHERE username=?", (username,))
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def get_user(self, username):
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username=?", (username,))
            row = cursor.fetchone()
            return self._row_to_dict(cursor, row) if row else None

    def get_user_by_id(self, user_id: int):
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE id=?", (user_id,))
            row = cursor.fetchone()
            return self._row_to_dict(cursor, row) if row else None

    def list_users(self) -> List[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, role, full_name, email, avatar, active, created_at FROM users")
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]
            result = []
            for row in rows:
                d = dict(zip(columns, row))
                if hasattr(d.get("created_at"), "isoformat"):
                    d["created_at"] = d["created_at"].isoformat()
                result.append(d)
            return result

    def get_user_by_email(self, email: str):
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE LOWER(email)=?", (email.lower(),))
            row = cursor.fetchone()
            return self._row_to_dict(cursor, row) if row else None

    def update_user_profile(self, user_id: int, **kwargs):
        allowed = {"full_name", "email", "avatar", "role"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [user_id]
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
            conn.commit()

    def update_user_password(self, user_id: int, password_hash: str):
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
            conn.commit()

    def update_reset_token(self, user_id: int, token: Optional[str], expires_at: Optional[str]):
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET reset_token = ?, reset_token_expires = ? WHERE id = ?", (token, expires_at, user_id))
            conn.commit()

    def get_user_by_reset_token(self, token: str) -> Optional[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE reset_token = ?", (token,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_dict(cursor, row)

    def update_user_status(self, user_id: int, active: int):
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET active = ? WHERE id = ?", (active, user_id))
            conn.commit()

    def update_user(self, user_id: int, **kwargs):
        allowed = {"username", "role", "email", "avatar", "full_name", "active"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [user_id]
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
            conn.commit()

    def delete_user(self, user_id: int):
        with self._conn() as conn:
            conn.cursor().execute("DELETE FROM users WHERE id=?", (user_id,))
            conn.commit()

    # ── Divisions ─────────────────────────────────────────────────────────────

    def list_divisions(self) -> List[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM divisions WHERE active = 1 ORDER BY id")
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, r)) for r in rows]

    def get_division(self, code: str) -> Optional[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM divisions WHERE code = ?", (code,))
            row = cursor.fetchone()
            return self._row_to_dict(cursor, row) if row else None

    def save_division(self, code: str, name: str, country: str = "", flag: str = "") -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                MERGE INTO divisions AS target
                USING (VALUES (?, ?, ?, ?)) AS source (code, name, country, flag)
                ON target.code = source.code
                WHEN MATCHED THEN
                    UPDATE SET name = source.name, country = source.country, flag = source.flag, active = 1
                WHEN NOT MATCHED THEN
                    INSERT (code, name, country, flag, active)
                    VALUES (source.code, source.name, source.country, source.flag, 1);
            """, (code, name, country, flag))
            conn.commit()
            cursor.execute("SELECT id FROM divisions WHERE code=?", (code,))
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def delete_division(self, code: str):
        with self._conn() as conn:
            conn.cursor().execute("UPDATE divisions SET active = 0 WHERE code = ?", (code,))
            conn.commit()

    # ── Alerts & Tracking ─────────────────────────────────────────────────────

    def save_alert(self, token: str, analysis_id: int, flux_id: str,
                   flux_name: str, label: str, n_critiques: int,
                   n_warnings: int, concordance: float,
                   anomalies: list, email_sent_to: str = "",
                   sla_meta: Optional[dict] = None,
                   workflow_status: str = "NEW",
                   severity: str = "") -> int:
        meta = sla_meta or {}
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO alerts
                   (token, analysis_id, flux_id, flux_name, label,
                    n_critiques, n_warnings, concordance, anomalies_json,
                    email_sent_to, status, workflow_status, sla_status, severity,
                    sla_breached,
                    sla_deadline, sla_hours, remaining_pct,
                    flux_type, severity_class, detected_at,
                    expected_hour, detection_latency_minutes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,'NEW',?,?,?,0,?,?,?,?,?,?,?,?)""",
                (token, analysis_id, flux_id, flux_name, label,
                 n_critiques, n_warnings, concordance,
                 json_encode(anomalies), email_sent_to,
                 workflow_status, "ON_TIME", severity,
                 meta.get("sla_deadline"),
                 meta.get("sla_hours"),
                 meta.get("remaining_pct", 100.0),
                 meta.get("flux_type"),
                 meta.get("severity_class"),
                 meta.get("detected_at"),
                 meta.get("expected_hour"),
                 meta.get("detection_latency_minutes")),
            )
            conn.commit()
            cursor.execute("SELECT @@IDENTITY")
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def get_alert_by_token(self, token: str) -> Optional[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM alerts WHERE token = ?", (token,))
            row = cursor.fetchone()
            if not row:
                return None
            d = self._row_to_dict(cursor, row)
            try:
                d["anomalies"] = json.loads(d["anomalies_json"])
            except Exception:
                d["anomalies"] = []
            return d

    def list_alerts(self, flux_id=None, limit=50, status_not_in=None) -> List[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            
            query = """SELECT id, token, analysis_id, flux_id, flux_name, label, 
                              n_critiques, n_warnings, concordance, status, 
                              email_sent_to, sla_breached, sla_deadline, 
                              sla_hours, remaining_pct, created_at,
                              flux_type, severity_class, detected_at,
                              expected_hour, detection_latency_minutes
                       FROM alerts"""
            params = []
            conditions = []
            
            if flux_id:
                conditions.append("flux_id=?")
                params.append(flux_id)
            
            if status_not_in:
                placeholders = ",".join(["?" for _ in status_not_in])
                conditions.append(f"status NOT IN ({placeholders})")
                params.extend(status_not_in)
            
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            
            query += " ORDER BY created_at DESC OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY"
            params.append(limit)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]
            result = []
            for row in rows:
                d = dict(zip(columns, row))
                if hasattr(d.get("created_at"), "isoformat"):
                    d["created_at"] = d["created_at"].isoformat()
                d["anomalies"] = []
                result.append(d)
            return result

    def update_alert_status(self, token: str, status: str, **kwargs) -> None:
        audit_username = kwargs.get("audit_username")
        audit_comment = kwargs.get("audit_comment", "")
        from_status = None
        with self._conn() as conn:
            cursor = conn.cursor()
            if audit_username:
                cursor.execute("SELECT status FROM alerts WHERE token = ?", (token,))
                row = cursor.fetchone()
                if row:
                    from_status = row[0]
            cursor.execute("UPDATE alerts SET status = ? WHERE token = ?", (status, token))
            conn.commit()
        if audit_username:
            self.save_alert_history(
                alert_token=token,
                username=audit_username,
                from_status=from_status,
                to_status=status,
                comment=audit_comment or f"{from_status or '?'} → {status}",
            )

    def delete_alert(self, token: str) -> None:
        """Supprime une alerte et ses données associées (tracking, historique, feedback)."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM alert_tracking WHERE alert_token = ?", (token,))
            cursor.execute("DELETE FROM alert_history WHERE alert_token = ?", (token,))
            cursor.execute("DELETE FROM ia_feedbacks WHERE alert_token = ?", (token,))
            cursor.execute("DELETE FROM alerts WHERE token = ?", (token,))
            conn.commit()

    def update_sla_fields(self, token: str, sla_data: dict) -> None:
        """Update SLA-related fields: sla_deadline, sla_hours, remaining_pct, breached."""
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """UPDATE alerts 
                   SET sla_deadline=?, sla_hours=?, remaining_pct=?, sla_breached=? 
                   WHERE token=?""",
                (
                    sla_data.get("sla_deadline"),
                    sla_data.get("sla_hours"),
                    sla_data.get("remaining_pct"),
                    1 if sla_data.get("breached") else 0,
                    token,
                ),
            )
            conn.commit()

    def flag_sla_breached(self, token: str) -> None:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE alerts SET sla_breached = 1 WHERE token = ?", (token,))
            conn.commit()

    def save_tracking(self, alert_token: str, username: str,
                      action: str, comment: str = "") -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO alert_tracking (alert_token, username, action, comment) VALUES (?,?,?,?)",
                (alert_token, username, action, comment)
            )
            conn.commit()
            cursor.execute("SELECT @@IDENTITY")
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def get_tracking(self, alert_token: str) -> List[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM alert_tracking WHERE alert_token = ? ORDER BY created_at ASC", (alert_token,))
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]
            result = []
            for row in rows:
                d = dict(zip(columns, row))
                if hasattr(d.get("created_at"), "isoformat"):
                    d["created_at"] = d["created_at"].isoformat()
                result.append(d)
            return result

    # ── Correction History ────────────────────────────────────────────────────

    def save_correction(self, flux_id: str, error_type: str,
                        column_name: str, solution_applied: str,
                        was_effective: bool = True) -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO correction_history 
                   (flux_id, error_type, column_name, solution_applied, was_effective) 
                   VALUES (?,?,?,?,?)""",
                (flux_id, error_type, column_name, solution_applied, int(was_effective))
            )
            conn.commit()
            cursor.execute("SELECT @@IDENTITY")
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def get_similar_corrections(self, flux_id: str, error_type: str,
                                column_name: str = "", limit: int = 5) -> List[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM correction_history 
                   WHERE flux_id = ? AND error_type = ? 
                   ORDER BY was_effective DESC, created_at DESC 
                   OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY""",
                (flux_id, error_type, limit)
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]
            result = []
            for row in rows:
                d = dict(zip(columns, row))
                if hasattr(d.get("created_at"), "isoformat"):
                    d["created_at"] = d["created_at"].isoformat()
                result.append(d)
            return result

    # ── Smart Mappings ────────────────────────────────────────────────────────

    def save_smart_mapping(self, flux_key: str, cegid_col: str, oracle_col: str, username: str) -> None:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                MERGE INTO smart_mappings AS target
                USING (VALUES (?, ?, ?, ?)) AS source (flux_key, cegid_col, oracle_col, created_by)
                ON target.flux_key = source.flux_key AND target.cegid_col = source.cegid_col AND target.oracle_col = source.oracle_col
                WHEN MATCHED THEN
                    UPDATE SET usage_count = target.usage_count + 1, last_used = GETUTCDATE()
                WHEN NOT MATCHED THEN
                    INSERT (flux_key, cegid_col, oracle_col, usage_count, last_used, created_by)
                    VALUES (source.flux_key, source.cegid_col, source.oracle_col, 1, GETUTCDATE(), source.created_by);
            """, (flux_key, cegid_col, oracle_col, username))
            conn.commit()

    def load_learned_mapping(self, flux_key: str) -> dict:
        try:
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT cegid_col, oracle_col FROM smart_mappings WHERE flux_key = ? ORDER BY usage_count DESC",
                    (flux_key,)
                )
                rows = cursor.fetchall()
            return {r[0]: r[1] for r in rows}
        except Exception:
            return {}

    def list_smart_mappings(self) -> List[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT flux_key, cegid_col, oracle_col, usage_count, last_used, created_by FROM smart_mappings ORDER BY usage_count DESC")
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]
            result = []
            for row in rows:
                d = dict(zip(columns, row))
                if hasattr(d.get("last_used"), "isoformat"):
                    d["last_used"] = d["last_used"].isoformat()
                result.append(d)
            return result

    # ── Assistant Conversations ───────────────────────────────────────────────

    def create_conversation(self, user_id: str, title: str) -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO conversations (user_id, title) VALUES (?, ?)",
                (user_id, title)
            )
            conn.commit()
            cursor.execute("SELECT @@IDENTITY")
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def get_conversation(self, conv_id: int, user_id: str) -> Optional[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id))
            row = cursor.fetchone()
            return self._row_to_dict(cursor, row) if row else None

    def list_conversations(self, user_id: str, limit: int = 20) -> List[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, title, msg_count, summary, created_at, updated_at 
                   FROM conversations WHERE user_id = ? 
                   ORDER BY updated_at DESC 
                   OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY""",
                (user_id, limit)
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]
            result = []
            for row in rows:
                d = dict(zip(columns, row))
                for k in ("created_at", "updated_at"):
                    if hasattr(d.get(k), "isoformat"):
                        d[k] = d[k].isoformat()
                result.append(d)
            return result

    def delete_conversation(self, conv_id: int, user_id: str) -> None:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id))
            conn.commit()

    def save_message(self, conv_id: int, role: str, content: str, context_keys: list = None) -> None:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO messages (conversation_id, role, content, context_keys) VALUES (?,?,?,?)",
                (conv_id, role, content, json_encode(context_keys or []))
            )
            cursor.execute(
                "UPDATE conversations SET msg_count = msg_count + 1, updated_at = GETUTCDATE() WHERE id = ?",
                (conv_id,)
            )
            conn.commit()

    def get_conversation_messages(self, conv_id: int, limit: int = 40) -> List[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT role, content FROM messages 
                   WHERE conversation_id = ? 
                   ORDER BY created_at DESC 
                   OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY""",
                (conv_id, limit)
            )
            rows = cursor.fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

    def get_conversation_summary(self, conv_id: int) -> str:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT summary FROM conversations WHERE id = ?", (conv_id,))
            row = cursor.fetchone()
        return row[0] if (row and row[0]) else ""

    def update_conversation_summary(self, conv_id: int, summary: str) -> None:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE conversations SET summary = ?, updated_at = GETUTCDATE() WHERE id = ?", (summary, conv_id))
            conn.commit()

    def save_user_pattern(self, user_id: str, pattern: str) -> None:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                MERGE INTO user_patterns AS target
                USING (VALUES (?, ?)) AS source (user_id, pattern)
                ON target.user_id = source.user_id AND target.pattern = source.pattern
                WHEN MATCHED THEN
                    UPDATE SET count = target.count + 1, last_seen = GETUTCDATE()
                WHEN NOT MATCHED THEN
                    INSERT (user_id, pattern, count, last_seen)
                    VALUES (source.user_id, source.pattern, 1, GETUTCDATE());
            """, (user_id, pattern))
            conn.commit()

    def get_user_patterns(self, user_id: str, limit: int = 5) -> List[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT pattern, count FROM user_patterns 
                   WHERE user_id = ? ORDER BY count DESC 
                   OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY""",
                (user_id, limit)
            )
            rows = cursor.fetchall()
        return [{"pattern": r[0], "count": r[1]} for r in rows]

    # ── Ecarts (legacy) ───────────────────────────────────────────────────────

    def save_ecarts(self, ecarts: list) -> None:
        if not ecarts:
            return
        with self._conn() as conn:
            cursor = conn.cursor()
            for e in ecarts:
                cursor.execute("""
                    INSERT INTO ecarts 
                    (timestamp, flux_id, article_id, type_ecart, colonne, valeur_cegid, valeur_oracle, details, statut)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'nouveau')
                """, (
                    e.get("timestamp"),
                    e.get("flux_id"),
                    e.get("article_id"),
                    e.get("type_ecart"),
                    e.get("colonne"),
                    str(e.get("valeur_cegid", "") or ""),
                    str(e.get("valeur_oracle", "") or ""),
                    e.get("details"),
                ))
            conn.commit()

    def list_ecarts(self, flux_id: str, limit: int = 100) -> List[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM ecarts WHERE flux_id = ? 
                   ORDER BY timestamp DESC 
                   OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY""",
                (flux_id, limit)
            )
            rows = cursor.fetchall()
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, r)) for r in rows]

    def update_ecart_status(self, ecart_id: int, status: str) -> None:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE ecarts SET statut = ? WHERE id = ?", (status, ecart_id))
            conn.commit()

    # ── Blob Storage ──────────────────────────────────────────────────────────

    def upload_blob(self, container_name: str, blob_name: str, data: bytes) -> str:
        container_client = self.blob_service.get_container_client(container_name)
        blob_client = container_client.get_blob_client(blob_name)
        blob_client.upload_blob(data, overwrite=True)
        return blob_client.url

    def download_blob(self, container_name: str, blob_name: str) -> bytes:
        container_client = self.blob_service.get_container_client(container_name)
        blob_client = container_client.get_blob_client(blob_name)
        return blob_client.download_blob().readall()

    def list_blobs(self, container_name: str) -> List[str]:
        container_client = self.blob_service.get_container_client(container_name)
        return [blob.name for blob in container_client.list_blobs()]

    # Persistent Jobs MS SQL Implementation
    def save_job(self, job_id: str, job_type: str, status: str, progress: int, step_label: str, meta: dict = None) -> None:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO jobs (id, job_type, status, progress, step_label, meta_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (job_id, job_type, status, progress, step_label, json_encode(meta or {}))
            )
            conn.commit()

    def update_job(self, job_id: str, **kwargs) -> None:
        if not kwargs:
            return
        allowed = {"status", "progress", "step_label", "result_json", "error", "meta_json", "started_at", "ended_at"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        
        for k, v in list(updates.items()):
            if hasattr(v, "isoformat"):
                updates[k] = v.isoformat()
            elif isinstance(v, dict):
                updates[k] = json_encode(v)
                
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE jobs SET {set_clause} WHERE id = ?",
                list(updates.values()) + [job_id]
            )
            conn.commit()

    def get_job(self, job_id: str) -> Optional[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
            if not row:
                return None
            d = self._row_to_dict(cursor, row)
            if d.get("meta_json"):
                try:
                    d["meta"] = json.loads(d["meta_json"])
                except Exception:
                    d["meta"] = {}
            else:
                d["meta"] = {}
            return d

    def cleanup_jobs(self, cutoff_seconds: int) -> None:
        import datetime
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(seconds=cutoff_seconds)).isoformat()
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM jobs WHERE status IN ('DONE', 'ERROR', 'EXPIRED') AND (ended_at < ? OR created_at < ?)",
                (cutoff, cutoff)
            )
            conn.commit()

    def get_incomplete_jobs(self) -> List[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM jobs WHERE status IN ('PENDING', 'RUNNING')")
            rows = cursor.fetchall()
            return [self._row_to_dict(cursor, r) for r in rows]

    # Alert History MS SQL Implementation
    def save_alert_history(self, alert_token: str, username: str, from_status: Optional[str], to_status: str, comment: str) -> int:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO alert_history (alert_token, username, from_status, to_status, comment)
                   VALUES (?, ?, ?, ?, ?)""",
                (alert_token, username, from_status, to_status, comment)
            )
            conn.commit()
            cursor.execute("SELECT @@IDENTITY")
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def get_alert_history(self, alert_token: str) -> List[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM alert_history WHERE alert_token = ? ORDER BY created_at ASC", (alert_token,))
            rows = cursor.fetchall()
            return [self._row_to_dict(cursor, r) for r in rows]

    def _get_resolved_timestamp(self, alert_token: str):
        from core.sla_policy import parse_alert_datetime
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT TOP 1 created_at FROM alert_history
                   WHERE alert_token=? AND to_status='RESOLVED'
                   ORDER BY created_at DESC""",
                (alert_token,),
            )
            row = cursor.fetchone()
            if row:
                return parse_alert_datetime(row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0]))
            cursor.execute(
                """SELECT TOP 1 created_at FROM alert_tracking
                   WHERE alert_token=? AND action='RESOLVED'
                   ORDER BY created_at DESC""",
                (alert_token,),
            )
            row = cursor.fetchone()
            if row:
                return parse_alert_datetime(row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0]))
        return None

    def list_alerts_for_auto_close(self, hours: int = 48) -> List[dict]:
        import datetime as dt
        cutoff = (dt.datetime.utcnow() - dt.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT a.token, a.flux_id, a.flux_name, a.status, a.created_at
                   FROM alerts a
                   WHERE a.status = 'RESOLVED'
                     AND COALESCE(
                       (SELECT MAX(h.created_at) FROM alert_history h
                        WHERE h.alert_token = a.token AND h.to_status = 'RESOLVED'),
                       (SELECT MAX(t.created_at) FROM alert_tracking t
                        WHERE t.alert_token = a.token AND t.action = 'RESOLVED'),
                       a.created_at
                     ) <= ?""",
                (cutoff,),
            )
            rows = cursor.fetchall()
            return [self._row_to_dict(cursor, r) for r in rows]

    def get_sla_metrics(self, days: int = 30) -> dict:
        from core.sla_policy import parse_alert_datetime
        import datetime as dt

        def _metrics_for_period(period_days: int) -> dict:
            cutoff = (dt.datetime.utcnow() - dt.timedelta(days=period_days)).strftime("%Y-%m-%d %H:%M:%S")
            with self._conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*) FROM alerts WHERE status='IGNORED' AND created_at >= ?",
                    (cutoff,),
                )
                ignored = cursor.fetchone()[0]
                cursor.execute(
                    """SELECT token, created_at, sla_deadline, sla_breached, status
                       FROM alerts
                       WHERE status IN ('RESOLVED','CLOSED') AND created_at >= ?""",
                    (cutoff,),
                )
                rows = cursor.fetchall()
                columns = [c[0] for c in cursor.description]

            in_sla, late, mttr_min, mttr_n = 0, 0, 0.0, 0
            for row in rows:
                d = dict(zip(columns, row))
                if hasattr(d.get("created_at"), "isoformat"):
                    d["created_at"] = d["created_at"].isoformat()
                created = parse_alert_datetime(d["created_at"])
                resolved_at = self._get_resolved_timestamp(d["token"]) or created
                deadline_str = d.get("sla_deadline")
                if deadline_str:
                    if resolved_at <= parse_alert_datetime(str(deadline_str)):
                        in_sla += 1
                    else:
                        late += 1
                elif not d.get("sla_breached"):
                    in_sla += 1
                else:
                    late += 1
                mttr_min += max(0, (resolved_at - created).total_seconds() / 60)
                mttr_n += 1

            total = in_sla + late
            return {
                "period_days": period_days,
                "compliance_pct": round(in_sla / total * 100, 1) if total else 100.0,
                "resolved_in_sla": in_sla,
                "resolved_late": late,
                "mttr_hours": round(mttr_min / mttr_n / 60, 2) if mttr_n else 0.0,
                "ignored_count": ignored,
            }

        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT COUNT(*) FROM alerts
                   WHERE sla_breached = 1
                     AND status NOT IN ('IGNORED','CLOSED','RESOLVED')""",
            )
            breach_count = cursor.fetchone()[0]

        main = _metrics_for_period(days)
        return {
            "current_breaches": breach_count,
            "period_days": days,
            "compliance_pct": main["compliance_pct"],
            "resolved_in_sla": main["resolved_in_sla"],
            "resolved_late": main["resolved_late"],
            "mttr_hours": main["mttr_hours"],
            "ignored_count": main["ignored_count"],
            "trend_7d": _metrics_for_period(7),
            "trend_30d": _metrics_for_period(30),
        }

    def save_expected_flux(self, flux_id: str, division: str, expected_hour: str,
                           source_path: str, active: int = 1) -> None:
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM expected_flux WHERE flux_id = ?", (flux_id,))
            row = cursor.fetchone()
            if row:
                cursor.execute(
                    """UPDATE expected_flux 
                       SET division = ?, expected_hour = ?, source_path = ?, active = ?
                       WHERE flux_id = ?""",
                    (division, expected_hour, source_path, active, flux_id)
                )
            else:
                cursor.execute(
                    """INSERT INTO expected_flux 
                       (flux_id, division, expected_hour, source_path, active) 
                       VALUES (?, ?, ?, ?, ?)""",
                    (flux_id, division, expected_hour, source_path, active)
                )
            conn.commit()

    def list_expected_flux(self, active_only: bool = False) -> List[dict]:
        with self._conn() as conn:
            cursor = conn.cursor()
            if active_only:
                cursor.execute("SELECT * FROM expected_flux WHERE active = 1")
            else:
                cursor.execute("SELECT * FROM expected_flux")
            rows = cursor.fetchall()
            return [self._row_to_dict(cursor, r) for r in rows]

    def update_expected_flux(self, flux_id: str, **kwargs) -> None:
        allowed = {"division", "expected_hour", "source_path", "active", "last_check_at", "last_status"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [flux_id]
        with self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE expected_flux SET {set_clause} WHERE flux_id = ?", values)
            conn.commit()