"""
config.py — Configuration centralisée
Lit les variables d'environnement : local (.env) ou Azure App Service (App Settings)

Variables requises en production Azure :
  FLASK_ENV=production
  SECRET_KEY=<clé secrète>
  STORAGE_BACKEND=azure
  AZURE_SQL_CONNECTION_STRING=...
  AZURE_STORAGE_CONNECTION_STRING=...
  AZURE_BLOB_CONTAINER_NAME=flux-files
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

# Charge .env si présent (développement local uniquement)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv non installé en prod → ok, Azure App Settings prend le relais


# ══════════════════════════════════════════════════════════════════
# ENVIRONNEMENT
# ══════════════════════════════════════════════════════════════════

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()

def _bool_env(key: str, default: bool = False) -> bool:
    return _env(key, str(default)).lower() in ("1", "true", "yes")


# ══════════════════════════════════════════════════════════════════
# CLASSES DE CONFIG
# ══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class FlaskConfig:
    SECRET_KEY:          str  = field(default_factory=lambda: _env("SECRET_KEY"))
    DEBUG:               bool = field(default_factory=lambda: _bool_env("FLASK_DEBUG", False))
    MAX_CONTENT_LENGTH:  int  = 200 * 1024 * 1024  # 200 MB


@dataclass(frozen=True)
class LocalStorageConfig:
    """Stockage local - SQLite (legacy) ou MySQL."""
    # Utilise /tmp par defaut sur Azure pour eviter les problemes de permissions
    DB_PATH: Path = field(default_factory=lambda: Path(
        _env("LOCAL_DB_PATH", "/tmp/flux_monitor.db")
    ))
    MYSQL_URL: str = field(default_factory=lambda: _env(
        "MYSQL_URL", "mysql+pymysql://root:@127.0.0.1/flux_monitor"
    ))



@dataclass(frozen=True)
class AzureStorageConfig:
    """Azure SQL + Blob Storage — production."""
    SQL_CONNECTION_STRING:     str = field(default_factory=lambda: _env("AZURE_SQL_CONNECTION_STRING"))
    BLOB_CONNECTION_STRING:    str = field(default_factory=lambda: _env("AZURE_STORAGE_CONNECTION_STRING"))
    BLOB_CONTAINER_CEGID:      str = field(default_factory=lambda: _env("AZURE_BLOB_CONTAINER_CEGID",  "cegid-files"))
    BLOB_CONTAINER_ORACLE:     str = field(default_factory=lambda: _env("AZURE_BLOB_CONTAINER_ORACLE", "oracle-files"))
    BLOB_CONTAINER_RESULTS:    str = field(default_factory=lambda: _env("AZURE_BLOB_CONTAINER_RESULTS","flux-results"))

    def validate(self) -> list[str]:
        """Retourne la liste des variables manquantes."""
        missing = []
        if not self.SQL_CONNECTION_STRING:
            missing.append("AZURE_SQL_CONNECTION_STRING")
        if not self.BLOB_CONNECTION_STRING:
            missing.append("AZURE_STORAGE_CONNECTION_STRING")
        return missing


@dataclass(frozen=True)
class ComparatorConfig:
    """Paramètres métier par défaut — surchargeables via l'UI."""
    DEFAULT_JOIN_KEYS:   tuple = ("HEADER_ID",)
    DEFAULT_AMT_COL:     str   = "INVOICE_AMOUNT"
    DEFAULT_QTY_COL:     str   = "ITEM_QTY"
    DEFAULT_ITEM_COL:    str   = "ITEM_CODE"
    DEFAULT_OU_COL:      str   = "INV_ORG_CODE"
    DEFAULT_DOC_COL:     str   = "DOC_NUM"
    DEFAULT_DATE_COL:    str   = "TRANSACTION_DATE"
    DEFAULT_SEUIL:       float = 0.01
    SEUIL_CRITIQUE_MULT: float = 100.0   # seuil * 100 → CRITIQUE
    LOW_CONCORDANCE_PCT: float = 70.0    # alerte si concordance < 70%
    MAX_UPLOAD_MB:       int   = 200
    MAX_TABLE_ROWS:      int   = 10_000


@dataclass(frozen=True)
class AppConfig:
    ENV:           str              = field(default_factory=lambda: _env("FLASK_ENV", "development"))
    BACKEND:       str              = field(default_factory=lambda: _env("STORAGE_BACKEND", "local"))
    flask:         FlaskConfig      = field(default_factory=FlaskConfig)
    local:         LocalStorageConfig  = field(default_factory=LocalStorageConfig)
    azure:         AzureStorageConfig  = field(default_factory=AzureStorageConfig)
    comparator:    ComparatorConfig    = field(default_factory=ComparatorConfig)
    QUEUE_BACKEND: str              = field(default_factory=lambda: _env("QUEUE_BACKEND", "local"))
    ENABLE_SCHEDULER: bool          = field(default_factory=lambda: _bool_env("ENABLE_SCHEDULER", False))
    DEFAULT_CONSULTANT_EMAIL: str   = field(default_factory=lambda: _env("DEFAULT_CONSULTANT_EMAIL", ""))

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"

    @property
    def use_azure(self) -> bool:
        return self.BACKEND == "azure"

    @property
    def allow_google_mock(self) -> bool:
        import os
        if self.is_production:
            return False
        return os.environ.get("ALLOW_GOOGLE_MOCK", "false").lower() in ("1", "true", "yes")

    def validate(self) -> None:
        """Valide la config au démarrage — lève une exception si invalide (fail-closed)."""
        if not self.flask.SECRET_KEY:
            raise EnvironmentError(
                "[CONFIG] SECRET_KEY est obligatoire (tous environnements). "
                "Définis-la dans .env (local) ou App Settings (Azure) — aucun fallback n'est toléré."
            )
        if self.use_azure:
            missing = self.azure.validate()
            if missing:
                raise EnvironmentError(
                    f"[CONFIG] Variables Azure manquantes : {', '.join(missing)}\n"
                    f"Définis-les dans .env (local) ou App Settings (Azure)."
                )
        if self.is_production:
            admin_user = os.environ.get("ADMIN_USER", "").strip()
            admin_password = os.environ.get("ADMIN_PASSWORD", "")
            if not admin_user or not admin_password:
                raise EnvironmentError(
                    "[CONFIG] ADMIN_USER et ADMIN_PASSWORD sont obligatoires en production "
                    "(aucun compte par défaut n'est créé)."
                )


# ══════════════════════════════════════════════════════════════════
# INSTANCE GLOBALE — importée partout
# ══════════════════════════════════════════════════════════════════
settings = AppConfig()