from __future__ import annotations
import json
import os
import threading
import logging
from typing import List, Optional
from storage.base import BaseStorage, json_encode
from sqlalchemy import create_engine, text
from config import settings

log = logging.getLogger(__name__)

def translate_sql(sql: str) -> str:
    # Remplacement des concaténations de chaînes SQLite en MySQL
    sql = sql.replace("? || '%'", "CONCAT(?, '%')")
    sql = sql.replace("? || '%'", "CONCAT(?, '%')")
    
    # Remplacement de la syntaxe d'insertion spécifique à SQLite
    sql = sql.replace("INSERT OR REPLACE INTO", "REPLACE INTO")
    sql = sql.replace("INSERT OR IGNORE INTO", "INSERT IGNORE INTO")
    
    # Remplacement des fonctions temporelles SQLite par NOW() de MySQL
    sql = sql.replace("datetime('now')", "NOW()")
    sql = sql.replace("datetime('now', 'localtime')", "NOW()")
    sql = sql.replace("DATETIME('now')", "NOW()")
    
    # Remplacement du placeholder ? par %s pour MySQL / PyMySQL
    sql = sql.replace("?", "%s")
    
    return sql


class MySQLRow:
    def __init__(self, mapping, keys):
        self._mapping = mapping
        self._keys = list(keys)
        self._values = [mapping.get(k) for k in self._keys]

    def __getitem__(self, item):
        if isinstance(item, int):
            return self._values[item]
        return self._mapping[item]

    def get(self, key, default=None):
        return self._mapping.get(key, default)

    def keys(self):
        return self._keys

    def values(self):
        return self._values

    def items(self):
        return [(k, self._mapping.get(k)) for k in self._keys]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._keys)

    def __repr__(self):
        return f"MySQLRow({dict(self.items())})"


class MySQLCursorResultWrapper:
    def __init__(self, result):
        self.result = result
        try:
            self._keys = list(result.keys())
        except Exception:
            self._keys = []

    def fetchone(self):
        row = self.result.fetchone()
        if row is None:
            return None
        return MySQLRow(row._mapping, self._keys)

    def fetchall(self):
        rows = self.result.fetchall()
        return [MySQLRow(row._mapping, self._keys) for row in rows]

    @property
    def lastrowid(self):
        return self.result.lastrowid

    @property
    def description(self):
        return [(k, None, None, None, None, None, None) for k in self._keys]


class MySQLConnectionWrapper:
    def __init__(self, connection):
        self.connection = connection
        self.trans = connection.begin()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is not None:
                self.trans.rollback()
            else:
                self.trans.commit()
        except Exception:
            self.trans.rollback()
            raise
        finally:
            self.connection.close()

    def execute(self, sql, params=None):
        sql_translated = translate_sql(sql)
        if params is not None:
            # CORRECTIF : SQLAlchemy interprète une liste "nue" comme plusieurs
            # jeux de paramètres (executemany), pas un seul jeu de valeurs.
            # Il faut donc toujours convertir une liste en tuple avant de
            # l'envoyer à exec_driver_sql, sinon erreur "List argument must
            # consist only of tuples or dictionaries" dès qu'une liste de
            # valeurs simples est passée (ex: list(updates.values()) + [id]).
            if isinstance(params, list):
                params = tuple(params)
            elif not isinstance(params, (tuple, dict)):
                params = (params,)
            result = self.connection.exec_driver_sql(sql_translated, params)
        else:
            result = self.connection.exec_driver_sql(sql_translated)
        return MySQLCursorResultWrapper(result)

    def executescript(self, script):
        statements = script.split(";")
        for stmt in statements:
            stmt_clean = stmt.strip()
            if stmt_clean:
                self.execute(stmt_clean)

    def commit(self):
        self.trans.commit()
        self.trans = self.connection.begin()

    def rollback(self):
        self.trans.rollback()
        self.trans = self.connection.begin()


class LocalStorage(BaseStorage):
    _engine = None

    def __init__(self, db_path=None):
        self.db_path = db_path
        if LocalStorage._engine is None:
            mysql_url = settings.local.MYSQL_URL
            log.info("Connexion MySQL via SQLAlchemy sur %s", mysql_url.split("@")[-1])
            LocalStorage._engine = create_engine(
                mysql_url,
                pool_pre_ping=True,
                pool_size=10,
                max_overflow=20
            )

    def _conn(self):
        try:
            conn = LocalStorage._engine.connect()
            return MySQLConnectionWrapper(conn)
        except Exception as e:
            log.error("Erreur de connexion MySQL : %s", e)
            raise ConnectionError(f"Impossible de se connecter à MySQL : {str(e)}")

    def init_db(self):
        with self._conn() as conn:
            self._create_schema(conn)
            self._migrate_alerts_schema(conn)
            # Seed divisions métier
            divisions_seed = [
                ("DOHA",  "ABA Luxury Doha",                    "QA", "🇶🇦"),
                ("KWT",   "ABA WATCHES AND JEWELRY Luxury Kuwait","KW", "🇰🇼"),
                ("SPG",   "Sports Gate Technogym (PSG)",         "QA", "🇶🇦"),
                ("KSA",   "Platinum Sand KSA (PSC KSA)",         "SA", "🇸🇦"),
            ]
            for code, name, country, flag in divisions_seed:
                try:
                    conn.execute(
                        "INSERT IGNORE INTO divisions (code, name, country, flag) VALUES (?,?,?,?)",
                        (code, name, country, flag)
                    )
                except Exception as ex:
                    log.warning("Erreur seed divisions : %s", ex)

    def _create_schema(self, conn):
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INT PRIMARY KEY AUTO_INCREMENT,
                username VARCHAR(100) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(50) NOT NULL DEFAULT 'analyst',
                email VARCHAR(150) DEFAULT '',
                avatar LONGTEXT,
                full_name VARCHAR(150) DEFAULT '',
                active TINYINT DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS divisions (
                id INT PRIMARY KEY AUTO_INCREMENT,
                code VARCHAR(50) NOT NULL UNIQUE,
                name VARCHAR(150) NOT NULL,
                country VARCHAR(10),
                flag VARCHAR(20),
                active TINYINT DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS expected_flux (
                flux_id VARCHAR(100) PRIMARY KEY,
                division VARCHAR(50) NOT NULL,
                expected_hour VARCHAR(10) NOT NULL,
                source_path VARCHAR(500) NOT NULL,
                active TINYINT DEFAULT 1,
                last_check_at VARCHAR(100) NULL,
                last_status VARCHAR(100) NULL
            );

            CREATE TABLE IF NOT EXISTS smart_mappings (
                id INT PRIMARY KEY AUTO_INCREMENT,
                flux_key VARCHAR(100) NOT NULL,
                cegid_col VARCHAR(100) NOT NULL,
                oracle_col VARCHAR(100) NOT NULL,
                usage_count INT DEFAULT 1,
                last_used DATETIME NULL,
                created_by VARCHAR(100) NULL,
                UNIQUE KEY uq_mappings (flux_key, cegid_col, oracle_col),
                INDEX idx_smart_mappings_key (flux_key)
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id VARCHAR(255) PRIMARY KEY,
                job_type VARCHAR(100) NOT NULL,
                status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
                progress INT DEFAULT 0,
                step_label VARCHAR(255) NULL,
                result_json LONGTEXT NULL,
                error TEXT NULL,
                meta_json TEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at DATETIME NULL,
                ended_at DATETIME NULL,
                flux_id VARCHAR(100) NULL,
                blob_cegid VARCHAR(500) NULL,
                blob_oracle VARCHAR(500) NULL,
                analyst VARCHAR(100) NULL
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id INT PRIMARY KEY AUTO_INCREMENT,
                user_id VARCHAR(100) NOT NULL,
                title VARCHAR(255) NOT NULL DEFAULT 'Nouvelle conversation',
                summary TEXT NULL,
                msg_count INT DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INT PRIMARY KEY AUTO_INCREMENT,
                conversation_id INT NOT NULL,
                role VARCHAR(50) NOT NULL,
                content LONGTEXT NOT NULL,
                context_keys TEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS user_patterns (
                id INT PRIMARY KEY AUTO_INCREMENT,
                user_id VARCHAR(100) NOT NULL,
                pattern VARCHAR(255) NOT NULL,
                count INT DEFAULT 1,
                last_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_user_pattern (user_id, pattern)
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INT PRIMARY KEY AUTO_INCREMENT,
                token VARCHAR(100) NOT NULL UNIQUE,
                analysis_id INT NULL,
                flux_id VARCHAR(100) NOT NULL,
                flux_name VARCHAR(255) NOT NULL DEFAULT '',
                label VARCHAR(255) NOT NULL DEFAULT '',
                n_critiques INT DEFAULT 0,
                n_warnings INT DEFAULT 0,
                concordance DOUBLE DEFAULT 100.0,
                anomalies_json LONGTEXT NOT NULL,
                status VARCHAR(50) NOT NULL DEFAULT 'NEW',
                email_sent_to VARCHAR(255) DEFAULT '',
                sla_breached TINYINT DEFAULT 0,
                sla_deadline VARCHAR(100) NULL,
                sla_hours DOUBLE NULL,
                remaining_pct DOUBLE NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_alerts_token (token),
                INDEX idx_alerts_flux_id (flux_id),
                INDEX idx_alerts_status (status),
                INDEX idx_alerts_created (created_at)
            );

            CREATE TABLE IF NOT EXISTS alert_tracking (
                id INT PRIMARY KEY AUTO_INCREMENT,
                alert_token VARCHAR(100) NOT NULL,
                username VARCHAR(100) NOT NULL DEFAULT 'system',
                action VARCHAR(100) NOT NULL,
                comment TEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS alert_history (
                id INT PRIMARY KEY AUTO_INCREMENT,
                alert_token VARCHAR(100) NOT NULL,
                username VARCHAR(100) NOT NULL,
                from_status VARCHAR(50) NULL,
                to_status VARCHAR(50) NOT NULL,
                comment TEXT NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_alert_history_token (alert_token)
            );

            CREATE TABLE IF NOT EXISTS ia_feedbacks (
                id INT PRIMARY KEY AUTO_INCREMENT,
                alert_token VARCHAR(100) NOT NULL,
                flux_id VARCHAR(100) NOT NULL DEFAULT '',
                flux_name VARCHAR(255) NOT NULL DEFAULT '',
                n_critiques INT DEFAULT 0,
                n_warnings INT DEFAULT 0,
                anomalies_json LONGTEXT NULL,
                action_taken VARCHAR(255) NOT NULL DEFAULT '',
                resolution_hours DOUBLE NULL,
                feedback_score INT NOT NULL DEFAULT 3,
                feedback_comment TEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS correction_history (
                id INT PRIMARY KEY AUTO_INCREMENT,
                flux_id VARCHAR(100) NOT NULL,
                error_type VARCHAR(100) NOT NULL,
                column_name VARCHAR(100) NULL,
                solution_applied TEXT NOT NULL,
                was_effective TINYINT DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS analyses (
                id INT PRIMARY KEY AUTO_INCREMENT,
                flux_id VARCHAR(100) NOT NULL,
                label VARCHAR(255) NOT NULL DEFAULT '',
                summary LONGTEXT NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_analyses_flux (flux_id),
                INDEX idx_analyses_created (created_at)
            );

            CREATE TABLE IF NOT EXISTS ecarts (
                id INT PRIMARY KEY AUTO_INCREMENT,
                timestamp VARCHAR(100) NULL,
                flux_id VARCHAR(100) NULL,
                article_id VARCHAR(255) NULL,
                type_ecart VARCHAR(100) NULL,
                colonne VARCHAR(100) NULL,
                valeur_cegid TEXT NULL,
                valeur_oracle TEXT NULL,
                details TEXT NULL,
                statut VARCHAR(50) DEFAULT 'nouveau',
                INDEX idx_ecarts_flux (flux_id)
            );
        """)

        self._ensure_columns(conn, "alerts", {
            "sla_deadline": "VARCHAR(100)",
            "sla_hours": "DOUBLE",
            "remaining_pct": "DOUBLE",
            "flux_type": "VARCHAR(50) NULL",
            "severity_class": "VARCHAR(20) NULL",
            "detected_at": "VARCHAR(100) NULL",
            "expected_hour": "VARCHAR(10) NULL",
            "detection_latency_minutes": "DOUBLE NULL",
        })
        self._ensure_columns(conn, "users", {
            "email": "VARCHAR(150) DEFAULT ''",
            "avatar": "LONGTEXT",
            "full_name": "VARCHAR(150) DEFAULT ''",
            "active": "TINYINT DEFAULT 1",
            "reset_token": "VARCHAR(255) NULL",
            "reset_token_expires": "DATETIME NULL",
        })
        self._ensure_columns(conn, "jobs", {
            "flux_id":    "VARCHAR(100)",
            "blob_cegid": "VARCHAR(500)",
            "blob_oracle": "VARCHAR(500)",
            "analyst":    "VARCHAR(100)",
        })

    def _migrate_alerts_schema(self, conn) -> None:
        """Add new columns for workflow_status / sla_status dual-status model."""
        new_columns = {
            "workflow_status":      "VARCHAR(50) DEFAULT NULL",
            "sla_status":           "VARCHAR(50) DEFAULT 'ON_TIME'",
            "severity":             "VARCHAR(50) DEFAULT NULL",
            "escalated_by":         "VARCHAR(100) DEFAULT NULL",
            "escalated_to":         "VARCHAR(255) DEFAULT NULL",
            "escalated_at":         "DATETIME DEFAULT NULL",
            "resolved_by":          "VARCHAR(100) DEFAULT NULL",
            "resolved_at":          "DATETIME DEFAULT NULL",
            "breach_email_sent":    "TINYINT DEFAULT 0",
            "breach_report_sent":   "TINYINT DEFAULT 0",
            "flux_type":            "VARCHAR(100) DEFAULT NULL",
            "severity_class":       "VARCHAR(50) DEFAULT NULL",
            "detected_at":          "VARCHAR(100) DEFAULT NULL",
            "expected_hour":        "VARCHAR(50) DEFAULT NULL",
            "detection_latency_minutes": "DOUBLE DEFAULT NULL",
            "sla_warning_sent":     "TINYINT DEFAULT 0",
            "ignore_notification_sent": "TINYINT DEFAULT 0",
            "concordance_state":    "VARCHAR(20) DEFAULT NULL",
        }
        self._ensure_columns(conn, "alerts", new_columns)

        try:
            conn.execute(
                "UPDATE alerts SET workflow_status = status "
                "WHERE workflow_status IS NULL AND status IS NOT NULL"
            )
        except Exception as exc:
            log.warning("[Migration] Impossible de migrer workflow_status: %s", exc)

    def _ensure_columns(self, conn, table: str, columns: dict[str, str]) -> None:
        existing = {row["Field"] for row in conn.execute(f"SHOW COLUMNS FROM `{table}`").fetchall()}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE `{table}` ADD COLUMN `{name}` {definition}")

    def save_analysis(self, flux_id, label, summary) -> int:

        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO analyses (flux_id, label, summary) VALUES (?,?,?)",
                (flux_id, label, json_encode(summary))
            )
            return cur.lastrowid

    def get_analysis(self, analysis_id):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM analyses WHERE id=?", (analysis_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["summary"] = json.loads(d["summary"])
        except Exception:
            d["summary"] = {}
        return d

    def list_analyses(self, flux_id=None, limit=50):
        with self._conn() as conn:
            if flux_id:
                rows = conn.execute(
                    "SELECT * FROM analyses WHERE flux_id=? ORDER BY created_at DESC LIMIT ?",
                    (flux_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM analyses ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            try:
                d["summary"] = json.loads(d["summary"])
            except Exception:
                d["summary"] = {}
            result.append(d)
        return result

    def delete_analysis(self, analysis_id):
        with self._conn() as conn:
            conn.execute("DELETE FROM analyses WHERE id=?", (analysis_id,))

    def update_summary(self, analysis_id, summary):
        with self._conn() as conn:
            conn.execute(
                "UPDATE analyses SET summary=? WHERE id=?",
                (json_encode(summary), analysis_id)
            )

    def count_analyses(self, flux_id=None) -> int:
        with self._conn() as conn:
            if flux_id:
                row = conn.execute("SELECT COUNT(*) FROM analyses WHERE flux_id=?", (flux_id,)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM analyses").fetchone()
        return row[0] if row else 0

    def count_analyses_by_analyst(self, username: str) -> int:
        with self._conn() as conn:
            term1 = f'%"analyst":"{username}"%'
            term2 = f'%"analyst": "{username}"%'
            row = conn.execute(
                "SELECT COUNT(*) FROM analyses WHERE summary LIKE ? OR summary LIKE ?",
                (term1, term2)
            ).fetchone()
        return row[0] if row else 0

    # ── users ─────────────────────────────────────────────────────────
    def save_user(self, username, password_hash, role="analyst") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR REPLACE INTO users (username, password_hash, role) VALUES (?,?,?)",
                (username, password_hash, role)
            )
            return cur.lastrowid

    def get_user(self, username):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: int):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def list_users(self):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, username, role, email, avatar, full_name, active, created_at FROM users"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_user_by_email(self, email: str):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE LOWER(email)=?", (email.lower(),)).fetchone()
        return dict(row) if row else None

    def update_user_profile(self, user_id: int, **kwargs):
        allowed = ["full_name", "email", "avatar"]
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k}=?" for k in updates.keys())
        with self._conn() as conn:
            conn.execute(
                f"UPDATE users SET {set_clause} WHERE id=?",
                list(updates.values()) + [user_id]
            )

    def update_user_password(self, user_id: int, password_hash: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (password_hash, user_id)
            )

    def update_reset_token(self, user_id: int, token: Optional[str], expires_at: Optional[str]):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET reset_token=?, reset_token_expires=? WHERE id=?",
                (token, expires_at, user_id)
            )

    def get_user_by_reset_token(self, token: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE reset_token=?", (token,)).fetchone()
        return dict(row) if row else None

    def update_user_status(self, user_id: int, active: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET active=? WHERE id=?",
                (active, user_id)
            )

    def update_user(self, user_id: int, **kwargs):
        allowed = ["username", "role", "email", "avatar", "full_name", "active"]
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k}=?" for k in updates.keys())
        with self._conn() as conn:
            conn.execute(
                f"UPDATE users SET {set_clause} WHERE id=?",
                list(updates.values()) + [user_id]
            )

    def delete_user(self, user_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))

    # ── divisions ─────────────────────────────────────────────────────
    def list_divisions(self) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM divisions WHERE active=1 ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_division(self, code: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM divisions WHERE code=?", (code,)
            ).fetchone()
        return dict(row) if row else None

    def save_division(self, code: str, name: str, country: str = "", flag: str = "") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR REPLACE INTO divisions (code, name, country, flag) VALUES (?,?,?,?)",
                (code, name, country, flag)
            )
            return cur.lastrowid

    def delete_division(self, code: str):
        with self._conn() as conn:
            conn.execute("UPDATE divisions SET active=0 WHERE code=?", (code,))

    # ── alerts ────────────────────────────────────────────────────────
    def save_alert(self, token: str, analysis_id: int, flux_id: str,
                   flux_name: str, label: str, n_critiques: int,
                   n_warnings: int, concordance: float,
                   anomalies: list, email_sent_to: str = "",
                   sla_meta: Optional[dict] = None,
                   workflow_status: str = "NEW",
                   severity: str = "") -> int:
        meta = sla_meta or {}
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO alerts
                   (token, analysis_id, flux_id, flux_name, label,
                    n_critiques, n_warnings, concordance, anomalies_json,
                    email_sent_to, status, workflow_status, sla_status, severity,
                    sla_breached,
                    sla_deadline, sla_hours, remaining_pct,
                    flux_type, severity_class, detected_at,
                    expected_hour, detection_latency_minutes, concordance_state)
                   VALUES (?,?,?,?,?,?,?,?,?,?,'NEW',?,?,?,0,?,?,?,?,?,?,?,?,?)""",
                (
                    token, analysis_id, flux_id, flux_name, label,
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
                    meta.get("detection_latency_minutes"),
                    meta.get("concordance_state", ""),
                ),
            )
            return cur.lastrowid

    def get_alert_by_token(self, token: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM alerts WHERE token=?", (token,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["anomalies"] = json.loads(d["anomalies_json"])
        except Exception:
            d["anomalies"] = []
        return d

    def get_alert_by_token_prefix(self, prefix: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM alerts WHERE token LIKE ? || '%' LIMIT 1",
                (prefix,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["anomalies"] = json.loads(d["anomalies_json"])
        except Exception:
            d["anomalies"] = []
        return d

    def list_alerts(self, flux_id=None, limit=50, status_not_in=None, workflow_status=None) -> List[dict]:
        """
        List alerts with optional filtering. Excludes anomalies_json for performance.
        """
        with self._conn() as conn:
            query = """SELECT id, token, analysis_id, flux_id, flux_name, label, 
                              n_critiques, n_warnings, concordance, status,
                              workflow_status, sla_status, severity,
                              email_sent_to, sla_breached, sla_deadline, 
                              sla_hours, remaining_pct, created_at,
                              flux_type, severity_class, detected_at,
                              expected_hour, detection_latency_minutes,
                              breach_email_sent, breach_report_sent,
                              sla_warning_sent, ignore_notification_sent,
                              concordance_state,
                              escalated_by, escalated_to, resolved_by
                       FROM alerts"""
            params = []
            conditions = []
            
            if flux_id:
                conditions.append("flux_id=?")
                params.append(flux_id)
            
            if workflow_status:
                conditions.append("workflow_status=?")
                params.append(workflow_status)
            elif status_not_in:
                placeholders = ",".join(["?" for _ in status_not_in])
                conditions.append(f"workflow_status NOT IN ({placeholders})")
                params.extend(status_not_in)
            
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            
            rows = conn.execute(query, params).fetchall()
        
        result = []
        for row in rows:
            d = dict(row)
            d["anomalies"] = []
            result.append(d)
        return result

    def update_alert_status(self, token: str, status: str, **kwargs) -> None:
        """Met à jour le statut d'une alerte et enregistre alert_history si audit_username est fourni."""
        audit_username = kwargs.get("audit_username")
        audit_comment = kwargs.get("audit_comment", "")
        from_status = None
        with self._conn() as conn:
            if audit_username:
                row = conn.execute(
                    "SELECT workflow_status, status FROM alerts WHERE token=?", (token,)
                ).fetchone()
                if row:
                    from_status = (row["workflow_status"] if hasattr(row, "keys") else row[0]) or (row[1] if hasattr(row, "keys") else row[1])
            conn.execute(
                "UPDATE alerts SET status=?, workflow_status=? WHERE token=?",
                (status, status, token),
            )
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
            conn.execute("DELETE FROM alert_tracking WHERE alert_token=?", (token,))
            conn.execute("DELETE FROM alert_history WHERE alert_token=?", (token,))
            conn.execute("DELETE FROM ia_feedbacks WHERE alert_token=?", (token,))
            conn.execute("DELETE FROM alerts WHERE token=?", (token,))

    def update_sla_fields(self, token: str, sla_data: dict) -> None:
        """
        Update SLA-related fields: sla_deadline, sla_hours, remaining_pct, breached.
        """
        with self._conn() as conn:
            conn.execute(
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

    def flag_sla_breached(self, token: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE alerts SET sla_breached=1 WHERE token=?", (token,))

    def update_sla_status(self, token: str, sla_status: str, audit_username: str = "system") -> None:
        """Update sla_status and record in alert_history for audit trail."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT sla_status FROM alerts WHERE token=?", (token,)
            ).fetchone()
            from_status = (row["sla_status"] if hasattr(row, "keys") else row[0]) if row else "ON_TIME"
            conn.execute("UPDATE alerts SET sla_status=? WHERE token=?", (sla_status, token))
        self.save_alert_history(
            alert_token=token,
            username=audit_username,
            from_status=from_status,
            to_status=sla_status,
            comment=f"SLA: {from_status} → {sla_status}",
        )

    def set_breach_email_sent(self, token: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE alerts SET breach_email_sent=1 WHERE token=?", (token,))

    def set_breach_report_sent(self, token: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE alerts SET breach_report_sent=1 WHERE token=?", (token,))

    def set_sla_warning_sent(self, token: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE alerts SET sla_warning_sent=1 WHERE token=?", (token,))

    def set_ignore_notification_sent(self, token: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE alerts SET ignore_notification_sent=1 WHERE token=?", (token,))

    def set_resolved(self, token: str, username: str) -> None:
        """Set resolved_by and resolved_at on alert, and freeze SLA at resolution moment."""
        from core.sla_policy import recompute_sla_progress

        with self._conn() as conn:
            row = conn.execute(
                "SELECT sla_deadline, sla_hours, created_at FROM alerts WHERE token=?",
                (token,)
            ).fetchone()

            conn.execute(
                "UPDATE alerts SET resolved_by=?, resolved_at=NOW() WHERE token=?",
                (username, token),
            )

            if row:
                alert_data = {
                    "sla_deadline": row["sla_deadline"],
                    "sla_hours": row["sla_hours"],
                    "created_at": row["created_at"],
                }
                final_sla = recompute_sla_progress(alert_data)

                if final_sla["breached"]:
                    final_sla_status = "BREACHED"
                    sla_breached_val = 1
                else:
                    final_sla_status = "RESOLVED"
                    sla_breached_val = 0

                conn.execute(
                    """UPDATE alerts
                       SET sla_deadline=?, sla_hours=?, remaining_pct=?, sla_breached=?, sla_status=?
                       WHERE token=?""",
                    (
                        final_sla["sla_deadline"],
                        final_sla["sla_hours"],
                        final_sla["remaining_pct"],
                        sla_breached_val,
                        final_sla_status,
                        token,
                    ),
                )

                self.save_tracking(
                    alert_token=token,
                    username=username,
                    action=f"SLA_FINALIZED_{final_sla_status}",
                    comment=f"SLA arrêté à la résolution: remaining_pct={final_sla['remaining_pct']}%, breached={final_sla['breached']}",
                )

    def set_escalated(self, token: str, by_user: str, to_email: str) -> None:
        """Set escalated_by, escalated_to, and escalated_at on alert."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE alerts SET escalated_by=?, escalated_to=?, escalated_at=NOW() WHERE token=?",
                (by_user, to_email, token),
            )

    def get_users_for_flux(self, flux_id: str) -> List[dict]:
        """Return active users assigned to a flux/division."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM users WHERE active=1"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── alert_tracking ────────────────────────────────────────────────
    def save_tracking(self, alert_token: str, username: str,
                      action: str, comment: str = "") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO alert_tracking (alert_token, username, action, comment) VALUES (?,?,?,?)",
                (alert_token, username, action, comment)
            )
            return cur.lastrowid

    def get_tracking(self, alert_token: str) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM alert_tracking WHERE alert_token=? ORDER BY created_at ASC",
                (alert_token,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── correction_history ────────────────────────────────────────────
    def save_correction(self, flux_id: str, error_type: str,
                        column_name: str, solution_applied: str,
                        was_effective: bool = True) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO correction_history
                   (flux_id, error_type, column_name, solution_applied, was_effective)
                   VALUES (?,?,?,?,?)""",
                (flux_id, error_type, column_name, solution_applied, int(was_effective))
            )
            return cur.lastrowid

    def get_similar_corrections(self, flux_id: str, error_type: str,
                                column_name: str = "", limit: int = 5) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM correction_history
                   WHERE flux_id=? AND error_type=?
                   ORDER BY was_effective DESC, created_at DESC LIMIT ?""",
                (flux_id, error_type, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── smart_mappings ────────────────────────────────────────────────
    def save_smart_mapping(self, flux_key: str, cegid_col: str, oracle_col: str, username: str) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO smart_mappings (flux_key, cegid_col, oracle_col, usage_count, last_used, created_by)
                VALUES (?, ?, ?, 1, datetime('now'), ?)
                ON CONFLICT(flux_key, cegid_col, oracle_col)
                DO UPDATE SET usage_count = usage_count + 1, last_used = datetime('now')
            """, (flux_key, cegid_col, oracle_col, username))

    def load_learned_mapping(self, flux_key: str) -> dict:
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT cegid_col, oracle_col FROM smart_mappings WHERE flux_key=? ORDER BY usage_count DESC",
                    (flux_key,)
                ).fetchall()
            return {r["cegid_col"]: r["oracle_col"] for r in rows}
        except Exception:
            return {}

    def list_smart_mappings(self) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT flux_key, cegid_col, oracle_col, usage_count, last_used, created_by FROM smart_mappings ORDER BY usage_count DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── conversations ─────────────────────────────────────────────────
    def create_conversation(self, user_id: str, title: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO conversations (user_id, title) VALUES (?, ?)",
                (user_id, title)
            )
            return cur.lastrowid

    def get_conversation(self, conv_id: int, user_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id=? AND user_id=?",
                (conv_id, user_id)
            ).fetchone()
        return dict(row) if row else None

    def list_conversations(self, user_id: str, limit: int = 20) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, title, msg_count, summary, created_at, updated_at
                   FROM conversations WHERE user_id=?
                   ORDER BY updated_at DESC LIMIT ?""",
                (user_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_conversation(self, conv_id: int, user_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM conversations WHERE id=? AND user_id=?",
                (conv_id, user_id)
            )

    def save_message(self, conv_id: int, role: str, content: str, context_keys: list = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, context_keys) VALUES (?,?,?,?)",
                (conv_id, role, content, json_encode(context_keys or []))
            )
            conn.execute(
                "UPDATE conversations SET msg_count=msg_count+1, updated_at=datetime('now') WHERE id=?",
                (conv_id,)
            )

    def get_conversation_messages(self, conv_id: int, limit: int = 40) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
                (conv_id, limit)
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def get_conversation_summary(self, conv_id: int) -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT summary FROM conversations WHERE id=?", (conv_id,)).fetchone()
        return row["summary"] if (row and row["summary"]) else ""

    def update_conversation_summary(self, conv_id: int, summary: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE conversations SET summary=?, updated_at=datetime('now') WHERE id=?",
                (summary, conv_id)
            )

    def save_user_pattern(self, user_id: str, pattern: str) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO user_patterns (user_id, pattern, count, last_seen)
                VALUES (?, ?, 1, datetime('now'))
                ON CONFLICT(user_id, pattern) DO UPDATE SET
                    count=count+1, last_seen=datetime('now')
            """, (user_id, pattern))

    def get_user_patterns(self, user_id: str, limit: int = 5) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT pattern, count FROM user_patterns WHERE user_id=? ORDER BY count DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── ecarts ────────────────────────────────────────────────────────
    def save_ecarts(self, ecarts: list) -> None:
        if not ecarts:
            return
        with self._conn() as conn:
            for e in ecarts:
                conn.execute("""
                    INSERT INTO ecarts
                    (timestamp, flux_id, article_id, type_ecart, colonne, valeur_cegid, valeur_oracle, details)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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

    def list_ecarts(self, flux_id: str, limit: int = 100) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM ecarts
                WHERE flux_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (flux_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def update_ecart_status(self, ecart_id: int, status: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE ecarts SET statut = ? WHERE id = ?", (status, ecart_id))

    # Persistent Jobs SQLite Implementation
    def save_job(self, job_id: str, job_type: str, status: str, progress: int, step_label: str, meta: dict = None) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO jobs (id, job_type, status, progress, step_label, meta_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                 (job_id, job_type, status, progress, step_label, json_encode(meta or {}))
            )

    def update_job(self, job_id: str, **kwargs) -> None:
        if not kwargs:
            return
        allowed = {"status", "progress", "step_label", "result_json", "error", "meta_json", "started_at", "ended_at"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        
        # Format datetimes if they are datetime objects
        for k, v in list(updates.items()):
            if hasattr(v, "isoformat"):
                updates[k] = v.isoformat()
                
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        with self._conn() as conn:
            conn.execute(
                f"UPDATE jobs SET {set_clause} WHERE id = ?",
                list(updates.values()) + [job_id]
            )

    def get_job(self, job_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
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
        cutoff = (datetime.datetime.now() - datetime.timedelta(seconds=cutoff_seconds)).isoformat()
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM jobs WHERE status IN ('DONE', 'ERROR', 'EXPIRED') AND (ended_at < ? OR created_at < ?)",
                (cutoff, cutoff)
            )

    def get_incomplete_jobs(self) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM jobs WHERE status IN ('PENDING', 'RUNNING')").fetchall()
        return [dict(r) for r in rows]

    # Alert History SQLite Implementation
    def save_alert_history(self, alert_token: str, username: str, from_status: Optional[str], to_status: str, comment: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO alert_history (alert_token, username, from_status, to_status, comment)
                   VALUES (?, ?, ?, ?, ?)""",
                (alert_token, username, from_status, to_status, comment)
            )
            return cur.lastrowid

    def get_alert_history(self, alert_token: str) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM alert_history WHERE alert_token = ? ORDER BY created_at ASC",
                (alert_token,)
            ).fetchall()
        return [dict(r) for r in rows]

    def _get_resolved_timestamp(self, alert_token: str):
        """Date/heure de passage en RESOLVED (historique ou tracking)."""
        from core.sla_policy import parse_alert_datetime
        with self._conn() as conn:
            row = conn.execute(
                """SELECT created_at FROM alert_history
                   WHERE alert_token=? AND to_status='RESOLVED'
                   ORDER BY created_at DESC LIMIT 1""",
                (alert_token,),
            ).fetchone()
            if row:
                return parse_alert_datetime(row["created_at"])
            row = conn.execute(
                """SELECT created_at FROM alert_tracking
                   WHERE alert_token=? AND action='RESOLVED'
                   ORDER BY created_at DESC LIMIT 1""",
                (alert_token,),
            ).fetchone()
            if row:
                return parse_alert_datetime(row["created_at"])
        return None

    def list_alerts_for_auto_close(self, hours: int = 48) -> List[dict]:
        """Alertes RESOLVED depuis plus de `hours` heures."""
        import datetime as dt
        cutoff = (dt.datetime.utcnow() - dt.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        with self._conn() as conn:
            rows = conn.execute(
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
            ).fetchall()
        return [dict(r) for r in rows]

    def get_sla_metrics(self, days: int = 30) -> dict:
        """KPIs de conformité SLA sur une période glissante."""
        from core.sla_policy import parse_alert_datetime
        import datetime as dt

        def _metrics_for_period(period_days: int) -> dict:
            cutoff = (dt.datetime.utcnow() - dt.timedelta(days=period_days)).strftime("%Y-%m-%d %H:%M:%S")
            with self._conn() as conn:
                ignored = conn.execute(
                    "SELECT COUNT(*) AS c FROM alerts WHERE status='IGNORED' AND created_at >= ?",
                    (cutoff,),
                ).fetchone()["c"]
                rows = conn.execute(
                    """SELECT token, created_at, sla_deadline, sla_breached, status
                       FROM alerts
                       WHERE status IN ('RESOLVED','CLOSED') AND created_at >= ?""",
                    (cutoff,),
                ).fetchall()

            in_sla, late, mttr_min = 0, 0, 0.0
            mttr_n = 0
            for row in rows:
                created = parse_alert_datetime(row["created_at"])
                resolved_at = self._get_resolved_timestamp(row["token"]) or created
                deadline_str = row.get("sla_deadline")
                if deadline_str:
                    if resolved_at <= parse_alert_datetime(deadline_str):
                        in_sla += 1
                    else:
                        late += 1
                elif not row.get("sla_breached"):
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
            breach_count = conn.execute(
                """SELECT COUNT(*) AS c FROM alerts
                   WHERE sla_breached = 1
                     AND status NOT IN ('IGNORED','CLOSED','RESOLVED')""",
            ).fetchone()["c"]

        return {
            "current_breaches": breach_count,
            "period_days": days,
            **{k: v for k, v in _metrics_for_period(days).items() if k != "period_days"},
            "trend_7d": _metrics_for_period(7),
            "trend_30d": _metrics_for_period(30),
        }

    # ── async jobs ────────────────────────────────────────────────────
    def create_job_async(self, job_id: str, flux_id: str, analyst: str,
                         blob_cegid: str, blob_oracle: str) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO jobs 
                    (id, flux_id, analyst, blob_cegid, blob_oracle, 
                     job_type, status, progress, step_label)
                VALUES (?, ?, ?, ?, ?, 'analysis', 'PENDING', 0, 'En attente...')
            """, (job_id, flux_id, analyst, blob_cegid, blob_oracle))
            conn.commit()

    def update_job_async(self, job_id: str, status: str,
                         result=None, error: str = None) -> None:
        from datetime import datetime
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute("""
                UPDATE jobs SET
                    status      = ?,
                    result_json = ?,
                    error       = ?,
                    ended_at    = ?
                WHERE id = ?
            """, (status,
                   json_encode(result) if result else None,
                  error,
                  now if status in ('DONE', 'ERROR') else None,
                  job_id))
            conn.commit()

    def get_job_async(self, job_id: str) -> dict | None:
        import json
        with self._conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cursor.description]
            d = dict(zip(cols, row))
            if d.get("result_json"):
                try:
                    d["result"] = json.loads(d["result_json"])
                except Exception:
                    d["result"] = {}
            return d

    def save_expected_flux(self, flux_id: str, division: str, expected_hour: str,
                           source_path: str, active: int = 1) -> None:
        """
        Sauvegarde ou met à jour la configuration d'un flux attendu.
        """
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM expected_flux WHERE flux_id = ?", (flux_id,)).fetchone()
            if row:
                conn.execute(
                    """UPDATE expected_flux 
                       SET division = ?, expected_hour = ?, source_path = ?, active = ?
                       WHERE flux_id = ?""",
                    (division, expected_hour, source_path, active, flux_id)
                )
            else:
                conn.execute(
                    """INSERT INTO expected_flux 
                       (flux_id, division, expected_hour, source_path, active) 
                       VALUES (?, ?, ?, ?, ?)""",
                    (flux_id, division, expected_hour, source_path, active)
                )
            conn.commit()

    def list_expected_flux(self, active_only: bool = False) -> List[dict]:
        """
        Liste tous les flux attendus.
        """
        with self._conn() as conn:
            if active_only:
                rows = conn.execute("SELECT * FROM expected_flux WHERE active = 1").fetchall()
            else:
                rows = conn.execute("SELECT * FROM expected_flux").fetchall()
        return [dict(r) for r in rows]

    def update_expected_flux(self, flux_id: str, **kwargs) -> None:
        """
        Met à jour dynamiquement certains champs d'un flux attendu.
        """
        allowed = {"division", "expected_hour", "source_path", "active", "last_check_at", "last_status"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k}=?" for k in updates.keys())
        with self._conn() as conn:
            conn.execute(
                f"UPDATE expected_flux SET {set_clause} WHERE flux_id=?",
                list(updates.values()) + [flux_id]
            )
            conn.commit()

