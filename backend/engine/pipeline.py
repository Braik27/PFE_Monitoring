"""
engine/pipeline.py — v5

L'analyse est TOUJOURS globale : Cegid complet vs Oracle complet.
La division (KWT, KSA, SPG, DAW7A) est détectée depuis OPERATING_UNIT_CODE
mais elle sert UNIQUEMENT pour filtrer les rapports Excel envoyés aux clients.
Elle n'affecte pas l'analyse ni les anomalies détectées.

NOUVEAU v5 :
  - Après chaque comparaison réussie, les anomalies sont indexées dans FAISS
    via ai/vector_store.py pour enrichir le RAG de l'assistant.
  - L'indexation est silencieuse : une erreur ne bloque pas l'analyse.
"""
from __future__ import annotations
import logging
import os
from dataclasses import dataclass, field
from typing import List

from engine.flux_loader import FluxLoader, FluxConfig
from engine.generic_reader import GenericReader
from engine.generic_cleaner import GenericCleaner
from engine.generic_comparator import GenericComparator, PairResult, Anomaly

log = logging.getLogger(__name__)


@dataclass
class AnalysisRequest:
    flux_id: str
    label:   str
    pairs:   List[dict]
    # Division optionnelle — stockée dans le résultat mais n'affecte PAS l'analyse
    forced_division: str = ""


@dataclass
class AnalysisResult:
    flux_id:   str
    flux_name: str
    label:     str
    pairs:     List[PairResult] = field(default_factory=list)
    error:     str = ""
    # Divisions trouvées dans les fichiers (pour info et rapports)
    divisions_found: List[str] = field(default_factory=list)

    @property
    def total_critiques(self) -> int:
        return sum(p.n_critiques + p.n_missing_oracle + p.n_missing_cegid
                   for p in self.pairs)

    @property
    def total_warnings(self) -> int:
        return sum(p.n_warnings for p in self.pairs)

    @property
    def total_anomalies(self) -> int:
        return self.total_critiques + self.total_warnings

    @property
    def concordance_moyenne(self) -> float:
        if not self.pairs:
            return 0.0
        return round(sum(p.concordance for p in self.pairs) / len(self.pairs), 1)

    def to_dict(self) -> dict:
        return {
            "flux_id":             self.flux_id,
            "flux_name":           self.flux_name,
            "label":               self.label,
            "total_critiques":     self.total_critiques,
            "total_warnings":      self.total_warnings,
            "total_anomalies":     self.total_anomalies,
            "concordance_moyenne": self.concordance_moyenne,
            "n_pairs":             len(self.pairs),
            "pairs":               [p.to_dict() for p in self.pairs],
            "error":               self.error,
            "divisions_found":     self.divisions_found,
        }


def run_analysis(request: AnalysisRequest) -> AnalysisResult:
    """
    Lance l'analyse globale Cegid vs Oracle.
    Aucun split par division — l'analyse porte sur le fichier complet.
    Les divisions sont détectées pour information uniquement
    (utilisées plus tard pour générer les rapports par client).

    NOUVEAU : indexe les anomalies dans FAISS après chaque analyse réussie.
    """
    try:
        config: FluxConfig = FluxLoader.load(request.flux_id)
    except Exception as e:
        log.error("Flux '%s' introuvable: %s", request.flux_id, e)
        return AnalysisResult(
            flux_id=request.flux_id, flux_name=request.flux_id,
            label=request.label, error=str(e),
        )

    result     = AnalysisResult(flux_id=config.flux_id,
                                flux_name=config.flux_name,
                                label=request.label)
    reader     = GenericReader(config)
    cleaner    = GenericCleaner(config)
    comparator = GenericComparator(config)

    for pair in request.pairs:
        path_c     = pair.get("cegid", "")
        path_o     = pair.get("oracle", "")
        pair_label = pair.get("label",
                               f"{os.path.basename(path_c)} vs {os.path.basename(path_o)}")

        try:
            df_c, bad_c = reader.read(path_c, "CEGID")
            df_o, bad_o = reader.read(path_o, "ORACLE")
            df_c = cleaner.clean(df_c)
            df_o = cleaner.clean(df_o)

            # 2. Analyse GLOBALE — tout le fichier d'un coup
            pr = comparator.compare(
                df_c, df_o,
                label=pair_label,
                file_cegid=os.path.basename(path_c),
                file_oracle=os.path.basename(path_o),
            )

            # 2bis. Anomalies lignes CSV invalides
            if bad_c.get("count", 0) > 0:
                pr.anomalies.append(Anomaly(
                    error_type="LIGNES_CSV_INVALIDES",
                    severity="WARNING",
                    key_values={},
                    val_cegid=str(bad_c["count"]),
                    val_oracle="",
                    explication=(
                        f"{bad_c['source']} ({bad_c['filename']}) : "
                        f"{bad_c['count']} ligne(s) CSV mal formée(s) ignorée(s) "
                        f"lors de la lecture. "
                        f"Échantillon : {bad_c['samples'][:2]}"
                    ),
                    action="Corriger le format des lignes mal échappées dans le fichier source.",
                ))
            if bad_o.get("count", 0) > 0:
                pr.anomalies.append(Anomaly(
                    error_type="LIGNES_CSV_INVALIDES",
                    severity="WARNING",
                    key_values={},
                    val_cegid="",
                    val_oracle=str(bad_o["count"]),
                    explication=(
                        f"{bad_o['source']} ({bad_o['filename']}) : "
                        f"{bad_o['count']} ligne(s) CSV mal formée(s) ignorée(s) "
                        f"lors de la lecture. "
                        f"Échantillon : {bad_o['samples'][:2]}"
                    ),
                    action="Corriger le format des lignes mal échappées dans le fichier source.",
                ))

            result.pairs.append(pr)

            # 3. Détecte les divisions présentes dans le fichier (pour les rapports)
            try:
                if request.forced_division:
                    result.divisions_found = [request.forced_division]
                else:
                    from engine.division_splitter import get_division_summary
                    divs_c = get_division_summary(df_c)
                    divs_o = get_division_summary(df_o)
                    all_divs = set(divs_c.keys()) | set(divs_o.keys())
                    all_divs.discard("GLOBAL")
                    result.divisions_found = sorted(all_divs) if all_divs else ["GLOBAL"]
            except Exception as div_err:
                log.warning("Détection des divisions ignorée pour '%s': %s", pair_label, div_err)

        except Exception as e:
            log.error("Erreur paire '%s': %s", pair_label, e)
            err_pair = PairResult(
                flux_id=config.flux_id, label=pair_label,
                file_cegid=os.path.basename(path_c),
                file_oracle=os.path.basename(path_o),
            )
            err_pair.anomalies.append(Anomaly(
                error_type="ERREUR_LECTURE",
                severity="CRITIQUE",
                explication=str(e),
                action="Vérifier le format et les noms de colonnes du fichier.",
            ))
            result.pairs.append(err_pair)

    log.info("Analyse '%s' — %d critiques, %d warnings, divisions=%s",
             config.flux_id, result.total_critiques,
             result.total_warnings, result.divisions_found)

    # ─────────────────────────────────────────────────────────
    # NOUVEAU : Indexation vectorielle des anomalies dans FAISS
    # Silencieuse — une erreur ici ne bloque pas l'analyse
    # ─────────────────────────────────────────────────────────
    _index_anomalies(result, config)

    return result


def _index_anomalies(result: AnalysisResult, config: FluxConfig):
    """
    Indexe les anomalies de l'analyse dans FAISS pour le RAG.
    Appelé en fin de run_analysis(), ne lève jamais d'exception.
    """
    try:
        from ai.vector_store import store_anomalies
        division = result.divisions_found[0] if result.divisions_found else "GLOBAL"
        n = store_anomalies(
            analysis_result=result,
            flux_id=config.flux_id,
            flux_name=config.flux_name,
            division=division,
        )
        if n > 0:
            log.info("[VectorStore] %d anomalies indexées pour flux '%s'", n, config.flux_id)
    except Exception as e:
        # Ne jamais bloquer l'analyse à cause du vecteur store
        log.warning("[VectorStore] Indexation ignorée : %s", e)


def _count_rows(filepath: str) -> int:
    import pandas as pd
    try:
        p = filepath.lower()
        if p.endswith(".xlsx") or p.endswith(".xls"):
            df = pd.read_excel(filepath, header=None, dtype=str)
        else:
            for enc in ("utf-8-sig", "utf-8", "latin-1"):
                try:
                    df = pd.read_csv(filepath, header=None, dtype=str,
                                     encoding=enc, on_bad_lines="skip",
                                     keep_default_na=False)
                    break
                except Exception:
                    continue
            else:
                return 0
        return max(0, len(df) - 1)
    except Exception:
        return 0


def _count_columns(filepath: str) -> int:
    import pandas as pd
    try:
        p = filepath.lower()
        if p.endswith(".xlsx") or p.endswith(".xls"):
            df = pd.read_excel(filepath, header=None, dtype=str)
        else:
            for enc in ("utf-8-sig", "utf-8", "latin-1"):
                try:
                    df = pd.read_csv(filepath, header=None, dtype=str,
                                     encoding=enc, on_bad_lines="skip",
                                     keep_default_na=False)
                    break
                except Exception:
                    continue
            else:
                return 0
        return max(0, len(df.columns))
    except Exception:
        return 0