from __future__ import annotations
import json, os, re
from dataclasses import dataclass, field
from typing import List, Optional, Optional
import logging

log = logging.getLogger(__name__)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REGISTRY_DIR = os.environ.get("REGISTRY_DIR", os.path.join(PROJECT_ROOT, "registry"))

@dataclass
class ColumnConfig:
    name: str
    type: str
    normalize: str
    required: bool = False

@dataclass
class ComparisonRule:
    column: str
    tolerance: float
    severity: str

@dataclass
class DisplayConfig:
    color: str = "#3b82f6"
    icon: str = "📊"
    amount_column: str = ""
    date_column: str = ""

@dataclass
class FluxConfig:
    flux_id: str
    flux_name: str
    description: str
    active: bool
    key_columns: List[str]
    columns: List[ColumnConfig]
    comparison_rules: List[ComparisonRule]
    alert_threshold: dict
    display: DisplayConfig
    # Pré-traitement (filtre PrefiR, dédoublonnage, etc.)
    pre_processing: dict = field(default_factory=dict)
    # Nouveaux champs pour la Carte d'identité
    direction: str = "export"          # "export" (Cegid→Oracle) ou "import" (Oracle→Cegid)
    frequency: str = "Quotidienne à 08h00"
    objective: str = ""                # Objectif métier
    main_rule: str = ""                # Règle de transformation principale
    # SLA overrides (per-flux): {"critical_max": 50.0, "warning_max": 80.0, "sla_hours": {...}}
    alert_sla: Optional[dict] = None
    # Consultant email for this flux (overrides DEFAULT_CONSULTANT_EMAIL)
    consultant_email: Optional[str] = None

    @property
    def column_names(self): return [c.name for c in self.columns]
    @property
    def key_str(self): return " + ".join(self.key_columns)
    def get_column(self, name): return next((c for c in self.columns if c.name == name), None)
    def get_rule(self, column): return next((r for r in self.comparison_rules if r.column == column), None)
    def min_critiques(self): return self.alert_threshold.get("min_critiques", 1)


class FluxLoader:

    @staticmethod
    def load(flux_id: str) -> FluxConfig:
        path = FluxLoader._registry_path(flux_id)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Flux '{flux_id}' non trouvé. Disponibles : {FluxLoader.list_flux_ids()}")
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        config = FluxLoader._parse(raw)
        FluxLoader._validate(config)
        return config

    @staticmethod
    def list_all() -> List[FluxConfig]:
        result = []
        for flux_id in FluxLoader.list_flux_ids():
            try:
                c = FluxLoader.load(flux_id)
                if c.active:
                    result.append(c)
            except Exception as e:
                log.warning("Erreur flux '%s': %s", flux_id, e)
        return result

    @staticmethod
    def list_flux_ids() -> List[str]:
        if not os.path.isdir(REGISTRY_DIR): 
            log.warning("Registry directory not found: %s", REGISTRY_DIR)
            return []
        return [f.replace(".json","").upper() for f in os.listdir(REGISTRY_DIR) if f.endswith(".json")]

    @staticmethod
    def save(config_dict: dict) -> FluxConfig:
        flux_id = config_dict.get("flux_id","").upper().strip()
        if not flux_id: raise ValueError("flux_id obligatoire")
        config_dict["flux_id"] = flux_id
        with open(FluxLoader._registry_path(flux_id), "w", encoding="utf-8") as f:
            json.dump(config_dict, f, indent=2, ensure_ascii=False)
        return FluxLoader.load(flux_id)

    @staticmethod
    def delete(flux_id: str):
        path = FluxLoader._registry_path(flux_id)
        if os.path.exists(path): os.remove(path)

    @staticmethod
    def _registry_path(flux_id): return os.path.join(REGISTRY_DIR, f"{flux_id.lower()}.json")

    @staticmethod
    def _parse(raw: dict) -> FluxConfig:
        columns = [ColumnConfig(name=c["name"], type=c.get("type","text"),
                                normalize=c.get("normalize","none"), required=c.get("required",False))
                   for c in raw.get("columns", [])]
        # Clés composites : "ITEM_CODE;UNIT_PRICE" ou "A, B" → deux clés distinctes
        key_columns = [
            piece.upper().strip()
            for k in raw.get("key_columns", [])
            for piece in re.split(r"[;,]", str(k))
            if piece.strip()
        ]
        # Colonnes clés non déclarées dans "columns" → ajout automatique
        # (le formulaire de config ne gère que key_columns ; évite "clé absente des colonnes")
        known = {c.name for c in columns}
        for k in key_columns:
            if k not in known:
                columns.append(ColumnConfig(name=k, type="text", normalize="none", required=False))
        rules = [ComparisonRule(column=r["column"], tolerance=float(r.get("tolerance",0)),
                                severity=r.get("severity","WARNING"))
                 for r in raw.get("comparison_rules", [])]
        d = raw.get("display", {})
        return FluxConfig(
            flux_id=raw["flux_id"].upper(), flux_name=raw.get("flux_name", raw["flux_id"]),
            description=raw.get("description",""), active=raw.get("active", True),
            key_columns=key_columns,
            columns=columns, comparison_rules=rules,
            alert_threshold=raw.get("alert_threshold", {"min_critiques":1}),
            display=DisplayConfig(color=d.get("color","#3b82f6"), icon=d.get("icon","📊"),
                                  amount_column=d.get("amount_column",""), date_column=d.get("date_column","")),
            pre_processing=raw.get("pre_processing", {}),
            # Nouveaux champs Carte d'identité
            direction=raw.get("direction", "export"),
            frequency=raw.get("frequency", "Quotidienne à 08h00"),
            objective=raw.get("objective", ""),
            main_rule=raw.get("main_rule", ""),
            # SLA overrides and consultant
            alert_sla=raw.get("alert_sla"),
            consultant_email=raw.get("consultant_email"),
        )

    @staticmethod
    def _validate(config: FluxConfig):
        if not config.key_columns: raise ValueError(f"'{config.flux_id}': key_columns vide")
        for key in config.key_columns:
            if key not in config.column_names:
                raise ValueError(f"'{config.flux_id}': clé '{key}' absente des colonnes")