"""
Génère les fixtures de test pour les bugs Items et Customer Balance.
"""
import csv
import os
import random

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures")
os.makedirs(os.path.join(OUT_DIR, "item_flux"), exist_ok=True)
os.makedirs(os.path.join(OUT_DIR, "customer_balance"), exist_ok=True)

random.seed(42)

# ── Items fixtures ──────────────────────────────────────────────────────────
item_headers = [
    "ITEM_CODE","UNIT_PRICE","DESCRIPTION","CURRENCY",
    "ITEM_BARCODE","ITEM_COLOR","ITEM_SIZE","BRAND",
    "CATEGORY","ITEM_TYPE","STATUS","ITEM_UOM"
]

items_oracle = [
    ["1304014100","15.50","Widget Alpha (New)","USD","BAR001","RED","M","BRAND_A","CAT_1","TYPE_A","ACTIVE","PCS"],
    ["1304014101","22.00","Gadget Beta v2","USD","BAR002","BLU","L","BRAND_B","CAT_2","TYPE_B","ACTIVE","PCS"],
    ["1304014102","9.99","Part Gamma","EUR","BAR003","GRN","S","BRAND_A","CAT_1","TYPE_C","INACTIVE","PCS"],
    ["1304014103","45.00","Tool Delta Plus","USD","BAR004","BLK","XL","BRAND_C","CAT_3","TYPE_A","ACTIVE","PCS"],
    ["1304014104","12.75","Material Epsilon","USD","BAR005","YEL","M","BRAND_B","CAT_2","TYPE_B","ACTIVE","PCS"],
    ["1304014105","33.30","Component Zeta","EUR","BAR006","WHT","L","BRAND_A","CAT_1","TYPE_C","ACTIVE","PCS"],
]

# Cegid : même données SAUF description de 1304014100 modifiée volontairement
items_cegid = [row[:] for row in items_oracle]
items_cegid[0][2] = "Widget Alpha (Updated)"  # écart volontaire

with open(os.path.join(OUT_DIR, "item_flux", "test_P3_oracle.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(item_headers)
    w.writerows(items_oracle)

with open(os.path.join(OUT_DIR, "item_flux", "test_P3_cegid.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(item_headers)
    w.writerows(items_cegid)

# Autres fichiers Items (format oracle/cegid generique)
item_master_headers = ["ITEM_CODE","DESCRIPTION","UNIT_PRICE","BRAND","CATEGORY","STATUS"]
extra_oracle = [
    ["1000000001","Standard Item A","10.00","BRAND_X","CAT_A","ACTIVE"],
    ["1000000002","Premium Item B","25.50","BRAND_Y","CAT_B","ACTIVE"],
    ["1000000003","Basic Item C","5.00","BRAND_X","CAT_A","INACTIVE"],
]
extra_cegid = [row[:] for row in extra_oracle]
extra_cegid[1][1] = "Premium Item B Updated"

with open(os.path.join(OUT_DIR, "item_flux", "Item_Master_oracle_29-09-2025.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(item_master_headers)
    w.writerows(extra_oracle)

with open(os.path.join(OUT_DIR, "item_flux", "ItemsMaster_ItemsMaster_29-09-2025.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(item_master_headers)
    w.writerows(extra_cegid)

# ── Customer Balance fixtures ───────────────────────────────────────────────
cb_headers = ["PrefiR","CUSTOMER_SITE_NAME","CUSTOMER_SITE_NUMBER","CREDIT_LIMIT","CREDIT_BALANCE"]

# Generer des clients avec quelques cas de test
cb_oracle = []
cb_cegid = []

base_clients = [
    ("7748968", "MAJID AL FUTTAIM HYP. WLL(C/C)-WEST -ACC", 50000, 12000),
    ("1451423", "CROWNE PLAZA(THE BUSINESS)-AIRPORT-CTL", 30000, 5000),
    ("1000001", "AL RASHID TRADING CO", 20000, 3000),
    ("1000002", "GULF ELECTRIC CO-WEST", 15000, 0),
    ("1000003", "AL OBAIDI LLC", 25000, 8000),
    ("1000004", "FUTURE ELECTRONICS", 40000, 15000),
    ("1000005", "AL HABIB TRADING", 18000, 2000),
    ("1000006", "SABIC AFFILIATES", 60000, 25000),
    ("1000007", "AL RAJHI BANKING", 70000, 10000),
    ("1000008", "JARIR BOOKSTORE", 22000, 4000),
    ("1000009", "AL NAFEA TRADING", 12000, 1000),
    ("1000010", "AL BAWABAH EST", 16000, 3000),
]

# Oracle : 12 lignes supplémentaires avec des noms avec parenthèses
# Cegid : mêmes numéros mais noms sans parenthèses
import re

def _normalize_name(v):
    v = str(v).strip().upper()
    v = re.sub(r"[^A-Z0-9\s]", " ", v)
    v = re.sub(r"\s+", " ", v).strip()
    return v

# ...

for num, name_oracle, limit, balance in base_clients:
    name_cegid = _normalize_name(name_oracle)
    cb_oracle.append(["I", name_oracle, num, str(limit), str(balance)])
    cb_cegid.append(["I", name_cegid, num, str(limit), str(balance)])

# Oracle : 3 clients absents de Cegid (faux négatifs Cegid)
for i in range(3):
    num = f"9000{i}"
    cb_oracle.append(["I", f"MISSING ORACLE {i}", num, str(10000 + i*1000), str(500 + i*100)])

# Cegid : 2 clients absents d'Oracle
for i in range(2):
    num = f"8000{i}"
    cb_cegid.append(["I", f"MISSING CEGID {i}", num, str(10000 + i*1000), str(500 + i*100)])

# Oracle : 12 lignes avec point-virgule non échappé dans CUSTOMER_SITE_NAME
for i in range(12):
    num = f"700{i}"
    name = f"ULTIMATE TRDG & CONTG CO;-Salwa-SCD;{num}"
    cb_oracle.append(["I", name, num, "10000", "500"])

# Cegid : ces 12 clients existent aussi (même numéro, nom corrigé)
for i in range(12):
    num = f"700{i}"
    name = f"ULTIMATE TRDG & CONTG CO Salwa SCD {num}"
    cb_cegid.append(["I", name, num, "10000", "500"])

# Oracle : 12 lignes avec point-virgule non échappé dans CUSTOMER_SITE_NAME
with open(os.path.join(OUT_DIR, "customer_balance", "CustomerBalance_Oracle_1.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(cb_headers)
    for row in cb_oracle:
        # Écrire manuellement pour ne pas échapper le délimiteur dans le nom
        f.write(";".join(row) + "\n")

with open(os.path.join(OUT_DIR, "customer_balance", "CustomerBalance_cegid_1.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(cb_headers)
    for row in cb_cegid:
        f.write(";".join(row) + "\n")

print("Fixtures générées avec succès.")
