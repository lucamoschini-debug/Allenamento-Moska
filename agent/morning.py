#!/usr/bin/env python3
"""Messaggio del mattino: prossima seduta della scheda con i carichi suggeriti.

Legge gli allenamenti da Hevy (solo GET), applica regole deterministiche e
invia il messaggio su Telegram. Nessun modello linguistico, solo libreria
standard.

Uso:
    python agent/morning.py                 # calcola e invia
    python agent/morning.py --dry-run       # stampa senza inviare
    python agent/morning.py --dry-run --fixture agent/tests/fixture_workouts.json --today 2026-10-11
"""

import argparse
import datetime as dt
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEDA_PATH = os.path.join(HERE, "scheda.json")

HEVY_HOST = "api.hevyapp.com"
TELEGRAM_HOST = "api.telegram.org"
ALLOWED_HOSTS = {HEVY_HOST, TELEGRAM_HOST}

MAX_PAGES = 10  # 10 pagine x 10 allenamenti: storico più che sufficiente
MAX_CHARS = 3500
TARATURA = "taratura: scegli un carico con RPE 7-8 (2-3 ripetizioni in riserva)"
WEEKDAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


# --------------------------------------------------------------------------
# Fuso orario
# --------------------------------------------------------------------------

def _rome_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Rome")
    except Exception:
        return None


ROME = _rome_tz()


def _last_sunday(year, month):
    d = dt.date(year, month, 31 if month in (3, 10) else 30)
    return d - dt.timedelta(days=(d.weekday() - 6) % 7)


def to_rome(ts):
    """Converte un orario ISO di Hevy (UTC) in Europe/Rome."""
    t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    if ROME is not None:
        return t.astimezone(ROME)
    # Ripiego senza tzdata: CEST dall'ultima domenica di marzo a quella di ottobre (01:00 UTC).
    u = t.astimezone(dt.timezone.utc)
    start = dt.datetime.combine(_last_sunday(u.year, 3), dt.time(1), dt.timezone.utc)
    end = dt.datetime.combine(_last_sunday(u.year, 10), dt.time(1), dt.timezone.utc)
    offset = 2 if start <= u < end else 1
    return u.astimezone(dt.timezone(dt.timedelta(hours=offset)))


def today_rome():
    return to_rome(dt.datetime.now(dt.timezone.utc).isoformat()).date()


# --------------------------------------------------------------------------
# Rete: solo api.hevyapp.com (GET) e api.telegram.org (sendMessage)
# --------------------------------------------------------------------------

class FetchError(Exception):
    pass


def _check_host(url):
    host = urllib.parse.urlsplit(url).hostname
    if host not in ALLOWED_HOSTS:
        raise FetchError("host non consentito: %s" % host)


def hevy_get(path, params):
    """Unica funzione che parla con Hevy: costruisce solo richieste GET.
    La chiave API è aggiunta dal proxy, lo script non la gestisce."""
    url = "https://%s%s?%s" % (HEVY_HOST, path, urllib.parse.urlencode(params))
    _check_host(url)
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise FetchError("Hevy ha risposto HTTP %s su %s" % (e.code, path))
    except urllib.error.URLError as e:
        raise FetchError("Hevy non raggiungibile: %s" % e.reason)


def fetch_workouts():
    workouts = []
    page, page_count = 1, 1
    while page <= page_count and page <= MAX_PAGES:
        data = hevy_get("/v1/workouts", {"page": page, "pageSize": 10})
        workouts.extend(data.get("workouts", []))
        page_count = int(data.get("page_count", 1) or 1)
        page += 1
    return workouts


def telegram_send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise FetchError("TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID mancanti nell'ambiente")
    url = "https://%s/bot%s/sendMessage" % (TELEGRAM_HOST, token)
    _check_host(url)
    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Mai riportare l'URL: contiene il token.
        raise FetchError("Telegram ha risposto HTTP %s" % e.code)
    except urllib.error.URLError as e:
        raise FetchError("Telegram non raggiungibile: %s" % str(e.reason).replace(token, "***"))
    if not data.get("ok"):
        raise FetchError("Telegram: invio non riuscito (%s)" % data.get("description", "?"))
    return data.get("result", {}).get("message_id")


# --------------------------------------------------------------------------
# Riconoscimento sedute
# --------------------------------------------------------------------------

def norm(title):
    return " ".join((title or "").lower().split())


def session_titles(scheda):
    """{seduta: set di titoli normalizzati (scheda + legacy)}"""
    out = {}
    for name, s in scheda["sessions"].items():
        titles = set(norm(t) for t in s.get("legacy_titles", []))
        for ex in s["exercises"]:
            titles.update(norm(t) for t in ex.get("hevy_titles", []))
        out[name] = titles
    return out


def known_titles(scheda):
    k = set()
    for t in session_titles(scheda).values():
        k |= t
    return k


def workout_titles(workout, scheda):
    ignore = set(norm(t) for t in scheda.get("ignore_titles", []))
    return [e.get("title", "") for e in workout.get("exercises", []) if norm(e.get("title")) not in ignore]


def next_in_rotation(scheda, name):
    rot = scheda["rotation"]
    return rot[(rot.index(name) + 1) % len(rot)]


def classify(workouts, scheda):
    """Assegna una seduta a ogni allenamento (ordine cronologico).

    Punteggio = numero di esercizi registrati che compaiono negli hevy_titles
    della seduta (legacy compresi). A parità vince la seduta attesa in
    rotazione dopo la precedente riconosciuta, poi l'ordine della rotazione.
    """
    st = session_titles(scheda)
    rot = scheda["rotation"]
    result = []
    prev = None
    for w in sorted(workouts, key=lambda w: w["start_time"]):
        titles = set(norm(t) for t in workout_titles(w, scheda))
        scores = {name: len(titles & st[name]) for name in rot}
        best = max(scores.values()) if scores else 0
        label = None
        if best > 0:
            cands = [n for n in rot if scores[n] == best]
            expected = next_in_rotation(scheda, prev) if prev else None
            label = expected if expected in cands else cands[0]
            prev = label
        result.append((w, label))
    return result


# --------------------------------------------------------------------------
# Serie e carichi
# --------------------------------------------------------------------------

def _w(s):
    return float(s.get("weight_kg") or 0)


def _r(s):
    return int(s.get("reps") or 0)


def group_per_leg(sets):
    """Step-up: Hevy registra una riga per gamba; due righe consecutive = una serie."""
    out = []
    for i in range(0, len(sets), 2):
        pair = sets[i:i + 2]
        types = [p.get("type") for p in pair]
        out.append({
            "type": "warmup" if "warmup" in types else pair[0].get("type"),
            "weight_kg": min(_w(p) for p in pair),
            "reps": min(_r(p) for p in pair),
        })
    return out


def working_sets(sets):
    """Serie di lavoro: escluse le warmup. Se nessuna è marcata warmup e la
    prima ha carico < 85% di tutte le altre, la prima è riscaldamento.
    failure e dropset contano come lavoro."""
    sets = [s for s in sets if s.get("reps")]
    if any(s.get("type") == "warmup" for s in sets):
        return [s for s in sets if s.get("type") != "warmup"]
    if len(sets) >= 2:
        others = min(_w(s) for s in sets[1:])
        if _w(sets[0]) < 0.85 * others:
            return sets[1:]
    return sets


def history_for(exercise, classified, scheda):
    """Ultime esecuzioni (più recente per prima) dell'esercizio: [(data, serie di lavoro)].
    Si usano solo gli hevy_titles dell'esercizio (i legacy servono solo al
    riconoscimento della seduta)."""
    titles = set(norm(t) for t in exercise.get("hevy_titles", []))
    if not titles:
        return []
    hist = []
    for w, _label in sorted(classified, key=lambda x: x[0]["start_time"], reverse=True):
        sets = []
        for e in w.get("exercises", []):
            if norm(e.get("title")) in titles:
                sets.extend(e.get("sets", []))
        if not sets:
            continue
        if exercise.get("per_leg"):
            sets = group_per_leg(sets)
        ws = working_sets(sets)
        if ws:
            hist.append((to_rome(w["start_time"]).date(), ws))
    return hist


def at_top(ws, rep_max):
    return bool(ws) and all(_r(s) >= rep_max for s in ws)


def round_step(x, step):
    if not step:
        return round(x * 2) / 2
    n = x / step
    k = math.floor(n)
    if n - k > 0.5 + 1e-9:
        k += 1
    return k * step


def fmt_num(x):
    if x is None:
        return "?"
    x = round(float(x), 2)
    s = ("%d" % x) if x == int(x) else ("%g" % x)
    return s.replace(".", ",")


def fmt_loads(loads, unit):
    if all(l == 0 for l in loads):
        return "corpo libero"
    if len(set(loads)) == 1:
        txt = fmt_num(loads[0])
    else:
        txt = "/".join(fmt_num(l) for l in loads)
    return txt + (" kg per mano" if unit == "kg per mano" else " kg")


def fmt_last(day, ws, per_leg):
    reps = [_r(s) for s in ws]
    loads = [_w(s) for s in ws]
    r = str(reps[0]) if len(set(reps)) == 1 else "/".join(str(x) for x in reps)
    if per_leg:
        r += " per gamba"
    if all(l == 0 for l in loads):
        l = "corpo libero"
    elif len(set(loads)) == 1:
        l = fmt_num(loads[0]) + " kg"
    else:
        l = "/".join(fmt_num(x) for x in loads) + " kg"
    return "ultima volta %s: %dx%s a %s" % (day.strftime("%d/%m"), len(ws), r, l)


def phase(scheda, day):
    c = scheda["cycle"]
    if dt.date.fromisoformat(c["deload_start"]) <= day <= dt.date.fromisoformat(c["deload_end"]):
        return "deload"
    if day > dt.date.fromisoformat(c["end_date"]):
        return "ended"
    return "normal"


def deload(load, step, factor):
    if load == 0:
        return 0
    new = round_step(load * factor, step)
    if step and new >= load and load - step > 0:
        new = load - step
    return max(new, 0)


def suggest(exercise, history, ph, factor):
    """Restituisce (testo carico, testo obiettivo, ultima volta | None, nuovo esercizio?)"""
    n_sets = exercise["sets"]
    step = exercise.get("step_kg")
    unit = exercise.get("unit", "kg totali")
    rmin, rmax = exercise["rep_min"], exercise["rep_max"]
    per_leg = exercise.get("per_leg", False)

    if not history:
        start = exercise.get("start_load")
        if isinstance(start, list):
            start = [float(x) for x in start[:n_sets]]
        elif isinstance(start, (int, float)):
            start = [float(start)] * n_sets
        elif start == "corpo libero":
            start = [0.0] * n_sets
        else:
            return TARATURA, "", None, True
        if ph == "deload":
            start = [deload(l, step, factor) for l in start]
        if all(l == 0 for l in start):
            return "corpo libero", "", None, True
        return ("partenza " + fmt_loads(start, unit) + ", poi tara a RPE 7-8"), "", None, True

    last_day, last_ws = history[0]
    loads = [_w(s) for s in last_ws][:n_sets]
    while len(loads) < n_sets:
        loads.append(loads[-1])
    last_txt = fmt_last(last_day, last_ws, per_leg)

    if ph == "deload":
        loads = [deload(l, step, factor) for l in loads]
        return fmt_loads(loads, unit), "scarico, RPE 6", last_txt, False
    if ph == "ended":
        return fmt_loads(loads, unit), "stesso carico", last_txt, False

    top_now = at_top(last_ws, rmax)
    top_prev = len(history) >= 2 and at_top(history[1][1], rmax)
    if top_now and top_prev:
        if step and not all(l == 0 for l in loads):
            loads = [l + step for l in loads]
            return fmt_loads(loads, unit), "+%s kg: 2 sedute al tetto" % fmt_num(step), last_txt, False
        return fmt_loads(loads, unit), "al tetto, nessun gradino: mantieni", last_txt, False
    if top_now:
        return fmt_loads(loads, unit), "stesso carico, già al tetto: se si ripete +1 gradino", last_txt, False
    return fmt_loads(loads, unit), "stesso carico, +1-2 rip per serie fino a %d" % rmax, last_txt, False


# --------------------------------------------------------------------------
# Piano del giorno
# --------------------------------------------------------------------------

def plan_day(classified, scheda, today):
    """Decide cosa fare oggi.
    Ritorna dict: mode in {"train", "rest", "done", "start"}, session, tomorrow, last."""
    recognized = [(w, l) for (w, l) in classified if l]
    if not recognized:
        return {"mode": "start", "session": scheda["rotation"][0], "last": None}
    last_w, last_label = recognized[-1]
    last_day = to_rome(last_w["start_time"]).date()
    nxt = next_in_rotation(scheda, last_label)
    info = {"last": (last_label, last_day), "next": nxt}
    if last_day >= today:
        info["mode"] = "done"
        info["session"] = last_label
        return info
    if last_label == scheda["rest_after"] and (today - last_day).days <= 1:
        info["mode"] = "rest"
        info["session"] = nxt
        return info
    info["mode"] = "train"
    info["session"] = nxt
    return info


def walk_line(scheda, day):
    wk = scheda["walks"]
    d = wk["days"].get(day.isoformat())
    if d:
        return "Camminata: %s km alle %s, %s (%s)" % (fmt_num(d["km"]), d["time"], d["kind"], wk["note"])
    if day > dt.date.fromisoformat(wk["end"]):
        return "Camminata: " + wk["after_end_text"]
    return None


def unrecognized(classified, scheda, today, days=14):
    known = known_titles(scheda)
    seen = []
    for w, _l in classified:
        if (today - to_rome(w["start_time"]).date()).days > days:
            continue
        for t in workout_titles(w, scheda):
            if norm(t) not in known and t not in seen:
                seen.append(t)
    return seen


def session_block(name, classified, scheda, today, ph):
    s = scheda["sessions"][name]
    factor = scheda["cycle"].get("deload_factor", 0.9)
    lines = []
    new_ex, no_title = [], []
    for ex in s["exercises"]:
        hist = history_for(ex, classified, scheda)
        load, goal, last, is_new = suggest(ex, hist, ph, factor)
        reps = "%d" % ex["rep_min"] if ex["rep_min"] == ex["rep_max"] else "%d-%d" % (ex["rep_min"], ex["rep_max"])
        if ex.get("per_leg"):
            reps += " per gamba"
        line = "- %s — %dx%s — %s" % (ex["name"], ex["sets"], reps, load)
        extra = [x for x in (goal, last) if x]
        if extra:
            line += " (" + "; ".join(extra) + ")"
        if ex.get("notes") and ex["notes"] not in line:
            line += " [" + ex["notes"] + "]"
        lines.append(line)
        if is_new:
            new_ex.append(ex["name"])
        if not ex.get("hevy_titles"):
            no_title.append(ex["name"])
    return lines, new_ex, no_title


def build_message(classified, scheda, today):
    ph = phase(scheda, today)
    plan = plan_day(classified, scheda, today)
    date_txt = "%s %s" % (WEEKDAYS[today.weekday()], today.strftime("%d/%m/%Y"))
    out = ["Buongiorno Luca — " + date_txt]
    notes = []
    gn = scheda["general_notes"]
    core = scheda["core"]

    if ph == "deload":
        notes.append(scheda["cycle"]["deload_note"])
    elif ph == "ended":
        notes.append(scheda["cycle"]["end_note"])

    if plan["mode"] in ("rest", "done"):
        if plan["mode"] == "rest":
            lbl, d = plan["last"]
            out.append("Oggi riposo consigliato (ultimo allenamento: %s del %s)." % (lbl, d.strftime("%d/%m")))
            out.append("Recupero: idratazione, sonno, mobilità leggera.")
            out.append("Domani: %s (%s)" % (plan["session"], scheda["sessions"][plan["session"]]["duration"]))
        else:
            lbl, _d = plan["last"]
            out.append("Oggi hai già registrato un allenamento: %s." % lbl)
            if lbl == scheda["rest_after"]:
                out.append("Prossima seduta: %s, dopo 1-2 giorni di riposo." % plan["next"])
            else:
                out.append("Prossima seduta: %s (%s)" % (plan["next"], scheda["sessions"][plan["next"]]["duration"]))
        if plan["mode"] == "rest":
            out.append("Core: " + core["full_rest_day"])
        wl = walk_line(scheda, today)
        if wl:
            out.append(wl)
    else:
        name = plan["session"]
        s = scheda["sessions"][name]
        out.append("Oggi: %s (%s)" % (name, s["duration"]))
        lines, new_ex, no_title = session_block(name, classified, scheda, today, ph)
        out.extend(lines)
        if s.get("core"):
            out.append("Core: " + core["short"])
        wl = walk_line(scheda, today)
        if wl:
            out.append(wl)
        if new_ex:
            notes.append("Nuovi esercizi (senza storico): " + ", ".join(new_ex) + ".")
        if no_title:
            notes.append("Dopo la prima volta dimmi il nome in Hevy di: " + ", ".join(no_title) + ".")
        notes.append(gn["upper"] if name.startswith("Upper") else gn["lower"])
        notes.append(gn["always"])

    unk = unrecognized(classified, scheda, today)
    if unk:
        notes.append("Esercizi non riconosciuti: " + ", ".join(unk) + ".")
    if notes:
        out.append("Note: " + " ".join(notes))

    msg = "\n".join(out)
    if len(msg) > MAX_CHARS:
        msg = msg[:MAX_CHARS - 20].rstrip() + "\n[...troncato]"
    return msg


# --------------------------------------------------------------------------

def load_scheda(path=SCHEDA_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="stampa il messaggio senza inviarlo")
    ap.add_argument("--fixture", help="file JSON con allenamenti di prova (al posto di Hevy)")
    ap.add_argument("--today", help="data di riferimento AAAA-MM-GG (default: oggi, Europe/Rome)")
    args = ap.parse_args(argv)

    try:
        scheda = load_scheda()
        today = dt.date.fromisoformat(args.today) if args.today else today_rome()
        if args.fixture:
            with open(args.fixture, encoding="utf-8") as f:
                workouts = json.load(f)["workouts"]
        else:
            workouts = fetch_workouts()
        classified = classify(workouts, scheda)
        msg = build_message(classified, scheda, today)
        if args.dry_run:
            print(msg)
            print("\n[dry run: %d caratteri, non inviato]" % len(msg))
            return 0
        message_id = telegram_send(msg)
        print("OK: messaggio inviato (%d caratteri, message_id %s)" % (len(msg), message_id))
        return 0
    except FetchError as e:
        print("ERRORE: %s" % e, file=sys.stderr)
        return 1
    except Exception as e:  # nessun traceback: potrebbe contenere dati sensibili
        text = "%s: %s" % (type(e).__name__, e)
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if token:
            text = text.replace(token, "***")
        print("ERRORE imprevisto: " + text, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
