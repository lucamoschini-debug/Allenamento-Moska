#!/usr/bin/env python3
"""Export per il coach: routine Hevy, storico serie, riepilogo per esercizio, scheda vs routine.

Legge Hevy solo in GET (come morning.py) e scrive un file Excel con i dati reali:
salvalo FUORI dal repository (il repository contiene solo codice e dati inventati).

Uso:
    python agent/export_coach.py <cartella di uscita>

Richiede openpyxl (non fa parte della libreria standard; morning.py non lo usa).
Le formule del riepilogo si calcolano all'apertura in Excel/Numbers/LibreOffice.
"""
import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import morning  # noqa: E402

if len(sys.argv) != 2:
    sys.exit(__doc__)
OUT_DIR = os.path.abspath(sys.argv[1])
REPO = os.path.dirname(HERE)
if os.path.commonpath([OUT_DIR, REPO]) == REPO:
    sys.exit("ERRORE: la cartella di uscita è dentro il repository; scegline una fuori (il file contiene dati reali).")

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    sys.exit("ERRORE: serve openpyxl (pip install openpyxl)")

sch = morning.load_scheda()
workouts = sorted(morning.fetch_workouts(), key=lambda w: w["start_time"])
routines = morning.fetch_routines()
cl_all = morning.classify(workouts, sch)
label = {id(w): l for w, l in cl_all}
now = dt.datetime.now(dt.timezone.utc); today = morning.today_rome()

it = {}
for w in workouts:
    for e in w["exercises"]:
        it.setdefault(e["exercise_template_id"], e["title"])
total_from = {}
for sess in sch["sessions"].values():
    for x in sess["exercises"]:
        if x.get("hevy_total_from"):
            for t in x["hevy_titles"]:
                total_from[morning.norm(t)] = dt.date.fromisoformat(x["hevy_total_from"])

F = "Arial"
H = Font(name=F, bold=True, color="FFFFFF"); HF = PatternFill("solid", fgColor="305496")
B = Font(name=F); BB = Font(name=F, bold=True); IT = Font(name=F, italic=True)
thin = Side(style="thin", color="BFBFBF"); BD = Border(top=thin, bottom=thin, left=thin, right=thin)
WR = Alignment(wrap_text=True, vertical="top")
TYPE = {"warmup": "riscaldamento", "normal": "lavoro", "failure": "cedimento", "dropset": "dropset"}

def header(ws, row, cols, widths):
    for c, h in enumerate(cols, 1):
        cell = ws.cell(row, c, h); cell.font = H; cell.fill = HF; cell.alignment = WR; cell.border = BD
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

def put(ws, r, vals, font=B):
    for c, v in enumerate(vals, 1):
        cell = ws.cell(r, c, v); cell.font = font; cell.border = BD; cell.alignment = WR

wb = Workbook()
ws = wb.active; ws.title = "Routine Hevy"
ws["A1"] = "Routine Hevy attuali — esportate il %s (solo lettura dall'API Hevy)" % morning.to_rome(now.isoformat()).strftime("%d/%m/%Y %H:%M"); ws["A1"].font = Font(name=F, bold=True, size=13)
ws["A2"] = "Una riga per serie impostata nella routine. kg come impostati in Hevy (Stacco rumeno e Step-up: totale dei due manubri; Multipower: solo dischi)."; ws["A2"].font = IT
header(ws, 4, ["Routine", "Cartella", "Ultima modifica", "N°", "Esercizio (Hevy italiano)", "Esercizio (routine, inglese)", "Serie", "Tipo", "kg", "Ripetizioni", "Note esercizio"],
       [40, 16, 13, 5, 38, 36, 6, 14, 8, 11, 34])
folders = {}
try:
    folders = {f["id"]: f["title"] for f in morning.hevy_get("/v1/routine_folders", {"page": 1, "pageSize": 10}).get("routine_folders", [])}
except Exception:
    pass
r = 4
for rt in routines:
    for i, e in enumerate(rt["exercises"], 1):
        for j, st in enumerate(e["sets"], 1):
            r += 1
            put(ws, r, [rt["title"].strip(), folders.get(rt.get("folder_id"), ""), rt.get("updated_at", "")[:10], i,
                        it.get(e["exercise_template_id"], "(mai registrato)"), e["title"], j,
                        TYPE.get(st.get("type"), st.get("type")), st.get("weight_kg"), st.get("reps"), e.get("notes") or ""])
ws.freeze_panes = "E5"; ws.auto_filter.ref = "A4:K%d" % r

ws2 = wb.create_sheet("Storico serie")
ws2["A1"] = "Storico allenamenti Hevy — tutte le serie registrate (%d allenamenti, %s - %s)" % (
    len(workouts), morning.to_rome(workouts[0]["start_time"]).strftime("%d/%m/%Y"), morning.to_rome(workouts[-1]["start_time"]).strftime("%d/%m/%Y"))
ws2["A1"].font = Font(name=F, bold=True, size=13)
ws2["A2"] = "Orari in Europe/Rome. 'Seduta' = riconoscimento dello script dagli esercizi. 'kg per mano' per gli esercizi segnati in Hevy come totale dei due manubri. Volume = kg × ripetizioni (formula)."; ws2["A2"].font = IT
header(ws2, 4, ["Data", "Ora", "Seduta", "Titolo allenamento", "Esercizio", "Serie", "Tipo", "kg (come in Hevy)", "kg per mano", "Ripetizioni", "RPE", "Distanza (m)", "Durata (s)", "Volume (kg×rip)"],
       [11, 6, 10, 38, 40, 6, 14, 10, 9, 10, 6, 10, 9, 11])
r = 4
for w in workouts:
    t = morning.to_rome(w["start_time"])
    lab = label.get(id(w)) or ("Core" if morning.norm(w["title"]).startswith("core") else "")
    for e in w["exercises"]:
        tf = total_from.get(morning.norm(e["title"]))
        for j, st in enumerate(e["sets"], 1):
            r += 1
            kg = st.get("weight_kg")
            per_hand = round(kg / 2, 2) if (tf and kg is not None and t.date() >= tf) else None
            put(ws2, r, [t.date(), t.strftime("%H:%M"), lab, w["title"].strip(), e["title"], j,
                         TYPE.get(st.get("type"), st.get("type")), kg, per_hand, st.get("reps"), st.get("rpe"),
                         st.get("distance_meters"), st.get("duration_seconds"),
                         '=IF(AND(ISNUMBER(H%d),ISNUMBER(J%d)),H%d*J%d,"")' % (r, r, r, r)])
            ws2.cell(r, 1).number_format = "DD/MM/YYYY"
last2 = r
ws2.freeze_panes = "F5"; ws2.auto_filter.ref = "A4:N%d" % last2

ws3 = wb.create_sheet("Riepilogo esercizi")
ws3["A1"] = "Riepilogo per esercizio e data — formule sul foglio 'Storico serie' (serie non di riscaldamento)"; ws3["A1"].font = Font(name=F, bold=True, size=13)
ws3["A2"] = "Ordinato per esercizio, poi per data: si legge l'andamento dei carichi. Esclusi Core e Camminata."; ws3["A2"].font = IT
header(ws3, 4, ["Esercizio", "Data", "Seduta", "Serie di lavoro", "kg max", "kg min", "Ripetizioni totali", "Volume (kg×rip)"], [42, 11, 10, 9, 9, 9, 11, 13])
pairs = []
for w in workouts:
    lab = label.get(id(w))
    if not lab:
        continue
    d = morning.to_rome(w["start_time"]).date()
    for e in w["exercises"]:
        if morning.norm(e["title"]) == "camminata":
            continue
        k = (e["title"], d, lab)
        if k not in pairs:
            pairs.append(k)
pairs.sort(key=lambda k: (k[0].lower(), k[1]))
S = "'Storico serie'!"
rng = lambda col: "%s$%s$5:$%s$%d" % (S, col, col, last2)
r = 4; prev = None
for name, d, lab in pairs:
    r += 1
    crit = '%s,$A%d,%s,$B%d,%s,"<>riscaldamento"' % (rng("E"), r, rng("A"), r, rng("G"))
    put(ws3, r, [name, d, lab, "=COUNTIFS(%s)" % crit, "=_xlfn.MAXIFS(%s,%s)" % (rng("H"), crit),
                 "=_xlfn.MINIFS(%s,%s)" % (rng("H"), crit), "=SUMIFS(%s,%s)" % (rng("J"), crit), "=SUMIFS(%s,%s)" % (rng("N"), crit)])
    ws3.cell(r, 2).number_format = "DD/MM/YYYY"
    if name != prev:
        ws3.cell(r, 1).font = BB
    prev = name
ws3.freeze_panes = "B5"; ws3.auto_filter.ref = "A4:H%d" % r

ws4 = wb.create_sheet("Scheda vs routine")
ws4["A1"] = "Confronto tra scheda %s (%s) e routine Hevy — quello che lo script segnala nelle note" % (sch["version"], sch["version_id"]); ws4["A1"].font = Font(name=F, bold=True, size=13)
ws4["A2"] = "Regola attuale dello script: dove non coincidono, valgono serie e carichi della routine Hevy."; ws4["A2"].font = IT
header(ws4, 4, ["Seduta", "Differenza"], [12, 120])
r = 4
for name in sch["rotation"]:
    _, _, _, diffs = morning.session_block(name, cl_all, sch, today, morning.phase(sch, today), routines)
    for dff in diffs or ["nessuna differenza"]:
        r += 1; put(ws4, r, [name, dff])

out = os.path.join(OUT_DIR, "%s - Routine e storico carichi Hevy Luca.xlsx" % morning.to_rome(now.isoformat()).strftime("%Y%m%d-%H%M"))
wb.save(out)
print(out)
print("allenamenti:", len(workouts), "| ultimo:", morning.to_rome(workouts[-1]["start_time"]).strftime("%d/%m %H:%M"), workouts[-1]["title"].strip())
print("routine:", len(routines), "| ultima modifica:", max(x.get("updated_at", "") for x in routines)[:16])
