"""
api/daily_report.py — Rapport Excel journalier Flux Monitor

FORMAT EXACT identique au fichier client ABA_LUX_Monitoring.xlsx :
  Colonnes : Direction | Flow Name | IN/OUT Cegid | Staging | Oracle | Status | Comment
  Couleurs : OK=vert | TBC=orange | KO=rouge
  Structure : 1 onglet par jour nommé "JJ-MM-AAAA"

LOGIQUE DIVISION :
  - L'analyse est GLOBALE (pas de split lors du calcul)
  - La division est stockée dans l'analyse (summary.division)
  - Pour le rapport par division : on filtre les ANALYSES (pas les lignes)
  - Chaque analyse porte une division → 1 fichier Excel par division/client

Routes :
  GET /api/report/daily              → rapport du jour (toutes analyses)
  GET /api/report/daily?division=KWT → rapport Koweït uniquement
  GET /api/report/by-division        → ZIP : 1 fichier Excel par division
  GET /api/report/monthly            → rapport mensuel (1 onglet/jour)
  GET /api/report/divisions          → liste divisions disponibles
"""
from __future__ import annotations
import io, zipfile
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, send_file
from api.auth import require_auth
from storage import get_storage
from engine.division_splitter import detect_division_from_value

report_bp = Blueprint("report", __name__)

# ── openpyxl ─────────────────────────────────────────────────────────
try:
    from openpyxl import Workbook
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    OPENPYXL_OK = True
except ImportError:
    OPENPYXL_OK = False

# ── Couleurs (exactes du fichier client) ─────────────────────────────
C_OK     = "FF70AD47"   # vert
C_TBC    = "FFBE5014"   # orange
C_KO     = "FFFF0000"   # rouge
C_HEADER = "FF1F3864"   # bleu foncé en-tête colonnes
C_TITLE  = "FF2460E8"   # bleu titre ligne 1
C_ADF    = "FF2F5496"   # bleu ADF Execution
C_EXP_BG = "FFD6E4F0"   # fond ligne Export (Cegid→Oracle)
C_IMP_BG = "FFEDE7F6"   # fond ligne Import (Oracle→Cegid)
C_WHITE  = "FFFFFFFF"
C_BLACK  = "FF000000"
C_GREY   = "FF595959"

# ── Mapping divisions ─────────────────────────────────────────────────
# Détection : table canonique engine/division_splitter.py (OU_CODE_MAP +
# TEXT_KEYWORDS, dérivée d'OPERATING_UNIT_CODE). Affichage : noms clients.
DIV_LABELS = {
    "DOHA": "🇶🇦 ABA Luxury Doha",
    "KWT":  "🇰🇼 ABA WATCHES AND JEWELRY Luxury Kuwait",
    "SPG":  "🇸🇬 Sports Gate Technogym (PSG)",
    "KSA":  "🇸🇦 Platinum Sand KSA (PSC KSA)",
    "GLOBAL":"Toutes divisions",
}

# Alias d'affichage : le détecteur canonique émet "DAW7A" pour Qatar/Doha,
# le vocabulaire client (frontend, rapports) est "DOHA".
_DIV_ALIASES = {"DAW7A": "DOHA"}


def _analysis_day(a: dict) -> str:
    """
    Date AAAA-MM-JJ d'une analyse, tolérante au type de created_at :
    datetime.datetime / datetime.date (MySQL, stockage local) → strftime,
    str ISO (backend Azure) → tronqué à 10 caractères.
    Un slicing [:10] direct lève TypeError sur un datetime.
    """
    created = a.get("created_at", "")
    if hasattr(created, "strftime"):
        return created.strftime("%Y-%m-%d")
    return str(created or "")[:10]

def _normalize_div(div: str) -> str:
    """Applique les alias d'affichage (DAW7A → DOHA)."""
    d = (div or "").strip().upper()
    return _DIV_ALIASES.get(d, d)


def _analysis_divisions(a: dict) -> list:
    """
    Toutes les divisions d'une analyse — regroupement possible dans
    plusieurs fichiers si l'analyse couvre plusieurs divisions.

    Priorité : summary.division + summary.divisions_found (dérivés
    d'OPERATING_UNIT_CODE à l'analyse via division_splitter) → label.
    Retourne ["GLOBAL"] si rien de détecté.
    """
    s = a.get("summary", {}) or {}
    divs: list = []

    def _add(raw) -> None:
        d = _normalize_div(str(raw or ""))
        if d and d != "GLOBAL" and d not in divs:
            divs.append(d)

    _add(s.get("division"))
    for x in (s.get("divisions_found") or []):
        _add(x)
    if not divs:
        _add(detect_division_from_value(a.get("label", "")) or "")
    return divs or ["GLOBAL"]


# ── Helpers Excel ─────────────────────────────────────────────────────

def _f(c):
    return PatternFill("solid", fgColor=c)

def _font(bold=False, color=C_BLACK, size=10, italic=False):
    return Font(bold=bold, color=color, size=size, name="Aptos Narrow", italic=italic)

def _align(h="center", v="center"):
    return Alignment(horizontal=h, vertical=v)

def _border():
    s = Side(style="thin", color="FFB0B0B0")
    return Border(left=s, right=s, top=s, bottom=s)

def _setup_cols(ws):
    """Largeurs de colonnes simplifiées."""
    widths = {"A":18, "B":28, "C":12, "D":12, "E":10, "F":45}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w

def _write_title(ws, row_idx, text):
    """Ligne 1 : titre bleu fusionné A–F."""
    ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=6)
    c = ws.cell(row=row_idx, column=1, value=text)
    c.font      = _font(bold=True, color=C_WHITE, size=11)
    c.fill      = _f(C_TITLE)
    c.alignment = _align(h="left")
    ws.row_dimensions[row_idx].height = 20

def _write_header(ws, row_idx, flux_id=""):
    """Ligne 2 : en-têtes colonnes, fond bleu foncé."""
    cols = ["Direction", "Flow Name", "IN Cegid", "OUT Oracle", "Status", "Comment"]
    if flux_id == "CUSTOMERBALANCE":
        cols = ["Direction", "Flow Name", "IN Cegid", "OUT Oracle", "Nb Integrated", "Nb Rejected", "Status", "Comment"]
    for i, h in enumerate(cols, 1):
        c = ws.cell(row=row_idx, column=i, value=h)
        c.font      = _font(bold=True, color=C_WHITE, size=10)
        c.fill      = _f(C_HEADER)
        c.alignment = _align()
        c.border    = _border()
    ws.row_dimensions[row_idx].height = 17

def _write_data_row(ws, row_idx, direction, flow_name,
                    n_cegid, n_oracle, status, n_errors, comment,
                    flux_id="", n_integrated=0, n_rejected=0):
    """
    Écrit une ligne de données.
    Fond : bleu clair = Export (Cegid→Oracle) | violet clair = Import (Oracle→Cegid)
    Statut : vert=OK | orange=TBC | rouge=KO
    """
    is_export = str(direction).upper().startswith("CEGID")
    row_bg = C_EXP_BG if is_export else C_IMP_BG

    su = (status or "").upper()
    s_bg = (C_OK  if su == "OK"
            else C_TBC if su == "TBC"
            else C_KO  if su in ("KO","ERROR","ERREUR")
            else "FFFFFFFF")
    s_fc = C_BLACK if su == "OK" else C_WHITE

    # Si flux CustomerBalance, ajoute les colonnes Nb Integrated / Nb Rejected
    if flux_id == "CUSTOMERBALANCE":
        vals = [direction, flow_name, n_cegid, n_oracle, n_integrated, n_rejected, status, n_errors, comment]
    else:
        vals = [direction, flow_name, n_cegid, n_oracle, status, n_errors, comment]
    
    for i, val in enumerate(vals, 1):
        c = ws.cell(row=row_idx, column=i, value=val)
        c.border    = _border()
        # Alignement selon le nombre de colonnes
        if flux_id == "CUSTOMERBALANCE":
            c.alignment = _align(h="left" if i in (1, 2, 9) else "center")
        else:
            c.alignment = _align(h="left" if i in (1, 2, 7) else "center")
        
        if i <= 4:
            c.fill = _f(row_bg)
            c.font = _font(bold=(i in (3, 4)), size=10)
        elif i == 5 and flux_id == "CUSTOMERBALANCE":
            # Nb Integrated - toujours vert
            c.fill = _f(C_OK)
            c.font = _font(bold=True, color=C_BLACK, size=10)
        elif i == 6 and flux_id == "CUSTOMERBALANCE":
            # Nb Rejected - rouge si > 0, sinon vert
            err_bg = C_KO if n_rejected > 0 else C_OK
            err_fc = C_WHITE if n_rejected > 0 else C_BLACK
            c.fill = _f(err_bg)
            c.font = _font(bold=True, color=err_fc, size=10)
        elif (i == 5 and flux_id != "CUSTOMERBALANCE") or (i == 7 and flux_id == "CUSTOMERBALANCE"):
            c.fill = _f(s_bg)
            c.font = _font(bold=True, color=s_fc, size=10)
        elif (i == 6 and flux_id != "CUSTOMERBALANCE") or (i == 8 and flux_id == "CUSTOMERBALANCE"):
            err_bg = C_KO if isinstance(val, int) and val > 0 else row_bg
            err_fc = C_WHITE if isinstance(val, int) and val > 0 else C_BLACK
            c.fill = _f(err_bg)
            c.font = _font(bold=True, color=err_fc, size=10)
        else:
            c.font = _font(size=9, italic=True, color=C_GREY)
    ws.row_dimensions[row_idx].height = 15


def _write_legend_sheet(wb):
    """Onglet Legend simplifié."""
    ws = wb.create_sheet(title="Legend")
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 55
    rows = [
        ("Colonne",        "Signification"),
        ("IN Cegid",       "Nombre de lignes dans le fichier Cegid"),
        ("OUT Oracle",     "Nombre de lignes dans le fichier Oracle"),
        ("",               ""),
        ("Status",         "Signification"),
        ("OK",             "Toutes les données correspondent"),
        ("TBC",            "To Be Confirmed — écarts mineurs à vérifier"),
        ("KO",             "Erreurs critiques — intervention requise immédiate"),
        ("Errors",         "Nombre total d'erreurs (critiques + warnings)"),
    ]
    status_bg = {"OK": C_OK, "TBC": C_TBC, "KO": C_KO}
    for i, (a, b) in enumerate(rows, 1):
        c1 = ws.cell(row=i, column=1, value=a)
        c2 = ws.cell(row=i, column=2, value=b)
        if i == 1 or a == "Status":
            for c in (c1, c2):
                c.font = _font(bold=True, color=C_WHITE)
                c.fill = _f(C_HEADER)
        elif a in status_bg:
            fc = C_BLACK if a == "OK" else C_WHITE
            c1.fill = _f(status_bg[a])
            c1.font = _font(bold=True, color=fc)
        for c in (c1, c2):
            c.border    = _border()
            c.alignment = _align(h="left")


# ── Construction d'un onglet journée ─────────────────────────────────

def _build_sheet(ws, day_label: str, analyses: list, division_filter: str = ""):
    """
    Construit un onglet complet pour une journée.
    Une ligne par analyse/flux.
    Structure : Titre → En-tête → 1 ligne par flux
    """
    # Determine if any analysis is for CustomerBalance to adjust columns
    has_customerbalance = any(
        "CUSTOMERBALANCE" in (a.get("flux_id", "") + " " + a.get("label", "")).upper()
        for a in analyses
    )

    _setup_cols(ws)
    ws.freeze_panes = "A3"

    r = 1
    div_label = DIV_LABELS.get(division_filter, division_filter) if division_filter else ""
    title = f"  Flux Monitor — {day_label}" + (f" | {div_label}" if div_label else "")
    _write_title(ws, r, title)
    r += 1
    # Pass flux_id to header to show extra columns for CustomerBalance
    flux_id_header = "CUSTOMERBALANCE" if has_customerbalance else ""
    _write_header(ws, r, flux_id=flux_id_header)
    r += 1

    for a in analyses:
        s       = a.get("summary", {})
        pairs   = s.get("pairs", [])
        flux_id = a.get("flux_id", "")
        is_customerbalance = "CUSTOMERBALANCE" in (a.get("flux_id", "") + " " + a.get("label", "")).upper()

        from engine.flux_loader import FluxLoader
        try:
            cfg       = FluxLoader.load(flux_id)
            direction = "Oracle --> Cegid" if cfg.direction == "import" else "Cegid --> Oracle"
            flow_name = cfg.flux_name
        except Exception:
            direction = "Cegid --> Oracle"
            flow_name = flux_id

        for pair in pairs:
            n_cegid = pair.get("n_cegid")
            n_oracle= pair.get("n_oracle")
            
            n_crit = (pair.get("n_critiques", 0)
                       + pair.get("n_missing_oracle", 0)
                       + pair.get("n_missing_cegid", 0))
            n_warn = pair.get("n_warnings", 0)
            n_errors = n_crit + n_warn
            conc   = pair.get("concordance", 100)

            if n_crit > 0:
                status = "KO"
            elif n_warn > 0 or conc < 100:
                status = "TBC"
            else:
                status = "OK"

            comment = _build_comment(pair, n_crit, n_warn, conc, a.get("label",""))

            # Get integrated/rejected counts for CustomerBalance
            n_integrated = pair.get("n_integrated", 0) if pair.get("n_integrated") is not None else 0
            n_rejected = pair.get("n_rejected", 0) if pair.get("n_rejected") is not None else 0

            _write_data_row(ws, r, direction, flow_name,
                            n_cegid or "—",
                            n_oracle or "—",
                            status, n_errors, comment,
                            flux_id="CUSTOMERBALANCE" if is_customerbalance else "",
                            n_integrated=n_integrated,
                            n_rejected=n_rejected)
            r += 1


def _build_comment(pair: dict, n_crit: int, n_warn: int, conc: float, label: str) -> str:
    """
    Génère un commentaire métier clair.
    Priorité : erreurs critiques → warnings → OK
    PAS de codes internes comme ERREUR_LECTURE.
    """
    anomalies = pair.get("anomalies", [])

    # Cas erreur de lecture — message explicite
    read_errors = [a for a in anomalies if a.get("error_type") == "ERREUR_LECTURE"]
    if read_errors:
        msg = read_errors[0].get("explication", "Erreur de lecture du fichier")
        # Nettoie le message : enlève les codes Python et les chemins
        for bad in ["ImportError","ModuleNotFoundError","No module named","engine.division_splitter"]:
            if bad in msg:
                return "Erreur d'import interne — contacter l'équipe technique"
        return f"Erreur lecture : {msg[:120]}"

    if n_crit == 0 and n_warn == 0 and conc >= 100:
        return "OK – données cohérentes"

    parts = []

    # Résumé chiffré
    n_c = pair.get("n_cegid", 0) or 0
    n_o = pair.get("n_oracle", 0) or 0
    if n_c != n_o and (n_c > 0 or n_o > 0):
        parts.append(f"Écart lignes : Cegid={n_c} | Oracle={n_o}")

    miss_o = pair.get("n_missing_oracle", 0)
    miss_c = pair.get("n_missing_cegid", 0)
    if miss_o > 0:
        parts.append(f"{miss_o} ligne(s) absente(s) dans Oracle")
    if miss_c > 0:
        parts.append(f"{miss_c} ligne(s) absente(s) dans Cegid")

    if n_crit > 0:
        parts.append(f"{n_crit} erreur(s) critique(s)")
    if n_warn > 0:
        parts.append(f"{n_warn} warning(s)")

    # Top colonnes en erreur
    top_cols = pair.get("top_error_columns", [])
    if top_cols:
        col_names = ", ".join(c["column"] for c in top_cols[:3])
        parts.append(f"Colonnes : {col_names}")

    return " | ".join(parts) if parts else f"Concordance {conc}%"


# ── Routes ────────────────────────────────────────────────────────────

@report_bp.get("/api/report/daily")
@require_auth
def daily_report():
    """
    Rapport du jour pour UNE division ou toutes.
    ?date=2026-04-13   (défaut = aujourd'hui)
    ?division=KWT      (filtre par division)
    ?flux_id=SALES     (filtre par flux)
    """
    if not OPENPYXL_OK:
        return jsonify({"error": "openpyxl non installé — pip install openpyxl"}), 500

    division = request.args.get("division", "").upper().strip()
    flux_id  = request.args.get("flux_id", "").upper().strip()
    date_str = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Format date invalide — AAAA-MM-JJ"}), 400

    # Récupère les analyses du jour
    all_a = get_storage().list_analyses(flux_id=flux_id or None, limit=1000)
    day_a = [a for a in all_a if _analysis_day(a) == date_str]

    # Filtre par division si demandé
    if division:
        division = _normalize_div(division)
        day_a = [a for a in day_a if division in _analysis_divisions(a)]

    if not day_a:
        return jsonify({
            "error": f"Aucune analyse pour le {date_str}"
                     + (f" (division {division})" if division else "")
        }), 404

    wb = Workbook()
    wb.remove(wb.active)
    day_label = target_date.strftime("%d-%m-%Y")

    ws = wb.create_sheet(title=day_label)
    _build_sheet(ws, day_label, day_a, division_filter=division)

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    fname = f"FluxMonitor_{date_str}{'_'+division if division else ''}.xlsx"
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@report_bp.get("/api/report/by-division")
@require_auth
def report_by_division():
    """
    Un fichier Excel par division → retourné dans un ZIP.
    Chaque fichier ne contient que les analyses de sa division.

    ?date=2026-04-13
    ?flux_id=SALES  (optionnel)

    Génère par exemple :
      FluxMonitor_KWT_2026-04-13.xlsx
      FluxMonitor_KSA_2026-04-13.xlsx
      FluxMonitor_SPG_2026-04-13.xlsx
      FluxMonitor_DAW7A_2026-04-13.xlsx
    """
    if not OPENPYXL_OK:
        return jsonify({"error": "openpyxl non installé"}), 500

    date_str = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    flux_id  = request.args.get("flux_id", "").upper().strip()

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Format date invalide — AAAA-MM-JJ"}), 400

    all_a = get_storage().list_analyses(flux_id=flux_id or None, limit=1000)
    day_a = [a for a in all_a if _analysis_day(a) == date_str]

    if not day_a:
        return jsonify({"error": f"Aucune analyse pour le {date_str}"}), 404

    # Groupe les analyses par division — une analyse multi-divisions
    # apparaît dans chaque fichier de division concerné
    groups: dict[str, list] = {}
    for a in day_a:
        for div in _analysis_divisions(a):
            groups.setdefault(div, []).append(a)

    day_label = target_date.strftime("%d-%m-%Y")

    # Si une seule division → rapport simple (pas de ZIP)
    if len(groups) <= 1:
        return daily_report()

    # Plusieurs divisions → ZIP
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for div, analyses in sorted(groups.items()):
            wb = Workbook(); wb.remove(wb.active)
            ws = wb.create_sheet(title=day_label)
            # Titre avec le nom de la division
            _build_sheet(ws, f"{day_label} | {DIV_LABELS.get(div, div)}", analyses)
            _write_legend_sheet(wb)

            buf = io.BytesIO(); wb.save(buf); buf.seek(0)
            # Noms des fichiers pour les clients
            div_clean = DIV_LABELS.get(div, div).replace("🇰🇼 ","").replace("🇶🇦 ","").replace("🇸🇦 ","").replace("🇸🇬 ","").replace("🇱🇺 ","")
            zf.writestr(f"report_{div.lower()}_{date_str}.xlsx", buf.read())

    zip_buf.seek(0)
    return send_file(zip_buf, as_attachment=True,
                     download_name=f"FluxMonitor_Divisions_{date_str}.zip",
                     mimetype="application/zip")


@report_bp.get("/api/report/monthly")
@require_auth
def monthly_report():
    """
    Rapport mensuel — un onglet par jour.
    ?month=2026-04
    ?division=KWT   (filtre par division)
    ?flux_id=SALES  (filtre par flux)
    """
    if not OPENPYXL_OK:
        return jsonify({"error": "openpyxl non installé"}), 500

    month_str = request.args.get("month", datetime.now().strftime("%Y-%m"))
    division  = request.args.get("division", "").upper().strip()
    flux_id   = request.args.get("flux_id", "").upper().strip()

    try:
        first_day = datetime.strptime(month_str + "-01", "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Format mois invalide — AAAA-MM"}), 400

    all_a = get_storage().list_analyses(flux_id=flux_id or None, limit=3000)
    wb = Workbook(); wb.remove(wb.active)
    sheets_created = 0

    current = first_day
    while current.month == first_day.month and current <= datetime.now():
        date_str  = current.strftime("%Y-%m-%d")
        day_label = current.strftime("%d-%m-%Y")

        day_a = [a for a in all_a if _analysis_day(a) == date_str]
        if division:
            division_n = _normalize_div(division)
            day_a = [a for a in day_a if division_n in _analysis_divisions(a)]

        if day_a:
            ws = wb.create_sheet(title=day_label)
            _build_sheet(ws, day_label, day_a)
            sheets_created += 1

        current += timedelta(days=1)

    if sheets_created == 0:
        return jsonify({"error": f"Aucune donnée pour {month_str}"}), 404

    _write_legend_sheet(wb)

    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    fname = f"FluxMonitor_{month_str}{'_'+division if division else ''}.xlsx"
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@report_bp.get("/api/report/divisions")
@require_auth
def list_divisions():
    """Divisions disponibles dans les analyses stockées."""
    analyses = get_storage().list_analyses(limit=1000)
    divs = set()
    for a in analyses:
        for d in _analysis_divisions(a):
            if d and d != "GLOBAL":
                divs.add(d)
    return jsonify(sorted(divs))