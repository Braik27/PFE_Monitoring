#!/usr/bin/env python3
"""
analyse_customer_balance.py

Script d'analyse pour le flux CustomerBalance (Import Oracle → Cegid).
Compte les lignes Rejected / Integrated dans le fichier Cegid 
et génère un rapport journalier avec les raisons de rejet.
"""
import os
import re
import csv
from collections import defaultdict, Counter
from datetime import datetime


def read_cegid_file(filepath):
    """
    Lit le fichier Cegid et retourne une liste de dicts.
    Format CSV attendu (séparateur ;):
    PrefiR;OPERATING_UNIT_CODE;CUSTOMER_SITE_NAME;CUSTOMER_SITE_NUMBER;CREDIT_LIMIT;CREDIT_BALANCE
    """
    rows = []
    known_encodings = ['utf-8-sig', 'utf-8', 'latin-1', 'cp1252']
    
    for enc in known_encodings:
        try:
            with open(filepath, 'r', encoding=enc, errors='replace') as f:
                # Essai de lecture CSV avec délimiteur ;
                sample = f.read(4096)
                f.seek(0)
                if ';' in sample:
                    delimiter = ';'
                elif ',' in sample:
                    delimiter = ','
                else:
                    delimiter = ';'
                reader = csv.DictReader(f, delimiter=delimiter)
                for row in reader:
                    rows.append(row)
            break
        except (UnicodeDecodeError, Exception):
            continue
    else:
        raise ValueError(f"Impossible de lire le fichier {filepath} avec les encodages connus")
    return rows


def read_oracle_file(filepath):
    """
    Lit le fichier Oracle et retourne une liste de dicts.
    Format CSV attendu (séparateur ;):
    OPERATING_UNIT_CODE;OPERATING_UNIT_NAME;CUSTOMER_SITE_NAME;CUSTOMER_SITE_NUMBER;CREDIT_LIMIT;CREDIT_BALANCE
    """
    return read_cegid_file(filepath)  # Même logique de lecture


def count_rejected(rows):
    """Compte les lignes rejetées (préfixe terminant par R)."""
    return sum(1 for r in rows if any(k.startswith('Prefi') for k in r.keys()) 
               and next(iter(r.values())).strip().upper().endswith('R'))


def count_integrated(rows):
    """Compte les lignes intégrées (préfixe terminant par I)."""
    return sum(1 for r in rows if any(k.startswith('Prefi') for k in r.keys()) 
               and next(iter(r.values())).strip().upper().endswith('I'))


def guess_rejection_reason(row, oracle_rows_by_key):
    """
    Tente de déterminer la raison du rejet d'une ligne.
    Renvoie une liste de raisons potentielles.
    """
    reasons = []
    
    # Trouve les valeurs de la ligne
    values = list(row.values())
    if not values:
        return reasons
    
    site_name = ''
    site_number = ''
    credit_limit = ''
    credit_balance = ''
    
    # Mapper les colonnes par index ou nom
    keys = list(row.keys())
    if len(values) >= 4:
        site_name = str(values[2]).strip() if len(values) > 2 else ''
        site_number = str(values[3]).strip() if len(values) > 3 else ''
        credit_limit = str(values[4]).strip() if len(values) > 4 else ''
        credit_balance = str(values[5]).strip() if len(values) > 5 else ''
    
    # Recherche la clé dans Oracle pour voir si elle existe
    oracle_match = oracle_rows_by_key.get(site_number.upper())
    
    if oracle_match:
        # Vérifie les valeurs limit/balance
        o_values = list(oracle_match.values())
        if len(o_values) >= 6:
            o_credit_limit = str(o_values[4]).strip() if len(o_values) > 4 else ''
            o_credit_balance = str(o_values[5]).strip() if len(o_values) > 5 else ''
            
            # Différence de valeurs numériques → possible cause de rejet
            if credit_limit != o_credit_limit:
                reasons.append(f"CREDIT_LIMIT mismatch: Cegid={credit_limit}, Oracle={o_credit_limit}")
            
            if credit_balance != o_credit_balance:
                reasons.append(f"CREDIT_BALANCE mismatch: Cegid={credit_balance}, Oracle={o_credit_balance}")
    else:
        reasons.append(f"Clé introuvable dans Oracle: {site_number} - {site_name}")
    
    if not reasons:
        reasons.append("Raison non déterminée automatiquement (erreur de validation Cegid)")
    
    return reasons


def analyze_customer_balance(cegid_path, oracle_path):
    """
    Analyse complète des fichiers CustomerBalance et retourne un résumé.
    """
    print("=== ANALYSE FLUX CUSTOMER BALANCE ===\n")
    
    # Lecture des fichiers
    cegid_rows = read_cegid_file(cegid_path)
    oracle_rows = read_oracle_file(oracle_path)
    
    print(f"Lignes Cegid total   : {len(cegid_rows)}")
    print(f"Lignes Oracle total  : {len(oracle_rows)}\n")
    
    # Compte Rejected / Integrated
    n_rejected = count_rejected(cegid_rows)
    n_integrated = count_integrated(cegid_rows)
    
    print(f"Lignes REJECTED (R)  : {n_rejected}")
    print(f"Lignes INTEGRATED (I): {n_integrated}")
    print(f"Total vérifié (R+I)  : {n_rejected + n_integrated}\n")
    
    # Index Oracle pour recherche rapide
    oracle_by_key = {}
    for o in oracle_rows:
        o_vals = list(o.values())
        if len(o_vals) >= 4:
            key = str(o_vals[3]).strip().upper()  # CUSTOMER_SITE_NUMBER
            oracle_by_key[key] = o
    
    # Collecte des raisons de rejet
    rejection_reasons = defaultdict(list)
    unknown_rejections = 0
    
    for row in cegid_rows:
        values = list(row.values())
        if not values:
            continue
        
        # Identifie si c'est une ligne R (Rejected)
        first_key = str(values[0]).strip().upper() if values else ''
        if first_key.endswith('R'):
            site_number = str(values[3]).strip() if len(values) > 3 else ''
            reasons = guess_rejection_reason(row, oracle_by_key)
            
            if reasons[0].startswith("Raison non déterminée"):
                unknown_rejections += 1
            
            for r in reasons:
                rejection_reasons[r].append(site_number)
    
    # Rapport des raisons de rejet
    print("=== RAISONS DE REJET (TOP) ===")
    if rejection_reasons:
        for reason, sites in sorted(rejection_reasons.items(), key=lambda x: -len(x[1])):
            print(f"  • {reason} : {len(sites)} lignes")
            # Affiche quelques exemples
            for site in sites[:3]:
                print(f"      - Site n°{site}")
    else:
        print("  Aucune ligne rejetée détectée.")
    
    print(f"\nLignes rejetées avec raison inconnue: {unknown_rejections}")
    
    # Résumé final
    summary = {
        "date_analysis": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_cegid_total": len(cegid_rows),
        "n_oracle_total": len(oracle_rows),
        "n_integrated": n_integrated,
        "n_rejected": n_rejected,
        "rejection_reasons_count": {k: len(v) for k, v in rejection_reasons.items()},
        "unknown_rejections": unknown_rejections
    }
    
    return summary


def generate_daily_report(cegid_path, oracle_path, output_dir="reports"):
    """
    Génère un rapport journalier au format CSV et texte.
    """
    import pathlib
    os.makedirs(output_dir, exist_ok=True)
    
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    
    summary = analyze_customer_balance(cegid_path, oracle_path)
    
    # Fichier texte
    report_txt = os.path.join(output_dir, f"CustomerBalance_Report_{date_str}.txt")
    with open(report_txt, 'w', encoding='utf-8') as f:
        f.write("=" * 60 + "\n")
        f.write("  RAPPORT JOURNALIER - FLUX CUSTOMER BALANCE\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Date du rapport : {date_str}\n\n")
        
        f.write(f"Fichier Cegid   : {os.path.basename(cegid_path)}\n")
        f.write(f"Fichier Oracle  : {os.path.basename(oracle_path)}\n\n")
        
        f.write(f"Total lignes Cegid  : {summary['n_cegid_total']}\n")
        f.write(f"Total lignes Oracle : {summary['n_oracle_total']}\n\n")
        
        f.write(f"LIGNES INTEGREES (I) : {summary['n_integrated']}\n")
        f.write(f"LIGNES REJECTED (R)  : {summary['n_rejected']}\n\n")
        
        f.write("RAISONS DE REJET:\n")
        f.write("-" * 60 + "\n")
        if summary['rejection_reasons_count']:
            for reason, count in sorted(summary['rejection_reasons_count'].items(), key=lambda x: -x[1]):
                f.write(f"  [{count}] {reason}\n")
        else:
            f.write("  Aucune ligne rejetée.\n")
        
        if summary['unknown_rejections'] > 0:
            f.write(f"\nRaison indéterminée  : {summary['unknown_rejections']}\n")
        
        f.write("\n" + "=" * 60 + "\n")
    
    print(f"\nRapport journalier généré: {report_txt}")
    
    # Fichier CSV simple pour import
    report_csv = os.path.join(output_dir, f"CustomerBalance_Report_{date_str}.csv")
    with open(report_csv, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(["Date", "Type", "Nombre", "Description"])
        writer.writerow([date_str, "Integrated", summary['n_integrated'], "Lignes intégrées dans Cegid (préfixe I)"])
        writer.writerow([date_str, "Rejected", summary['n_rejected'], "Lignes rejetées dans Cegid (préfixe R)"])
        for reason, count in summary['rejection_reasons_count'].items():
            writer.writerow([date_str, "Reason", count, reason])
    
    print(f"Données CSV générées     : {report_csv}")
    
    return summary, report_txt, report_csv


if __name__ == "__main__":
    import sys
    cegid_path = "CustomerBalance_cegid 1 (1).csv"
    oracle_path = "CustomerBalance_Oracle 1.csv"
    
    generate_daily_report(cegid_path, oracle_path)
