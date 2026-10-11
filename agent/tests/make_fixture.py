"""Genera fixture_workouts.json: allenamenti INVENTATI per i test (nessun dato reale)."""
import json, os

def s(w, r, t="normal"):
    return {"type": t, "weight_kg": w, "reps": r, "distance_meters": None, "duration_seconds": None}

def ex(title, sets):
    return {"title": title, "sets": sets}

def wk(start, title, exercises):
    return {"id": start, "title": title, "start_time": start, "end_time": start, "exercises": exercises}

W = [
    wk("2026-09-01T16:00:00+00:00", "Petto", [
        ex("Chest Press (Macchina)", [s(20, 12)] * 4),
        ex("Panca Inclinata (Manubrio)", [s(5, 12)] * 3),
        ex("Pushdown Tricipiti con Corda", [s(5, 12)] * 3),
    ]),
    wk("2026-09-02T16:00:00+00:00", "Gambe", [
        ex("Leg Press Orizzontale (Macchina)", [s(40, 10)] * 4),
        ex("Calf Raise in Piedi (Manubrio)", [s(0, 15)] * 3),
    ]),
    wk("2026-09-03T16:00:00+00:00", "Schiena qualcosa", [
        ex("Trazione verticale (macchina)", [s(25, 12)] * 4),
        ex("Rematore T Bar", [s(10, 12)] * 3),
        ex("Camminata", [{"type": "normal", "weight_kg": None, "reps": None, "distance_meters": 1000, "duration_seconds": 700}]),
    ]),
    wk("2026-09-04T16:00:00+00:00", "Spalle nuove", [
        ex("Shoulder Press Inventata", [s(5, 10)] * 4),
        ex("Alzate Laterali (Macchina)", [s(10, 15)] * 3),
        ex("Face Pull Inventato", [s(5, 12)] * 3),
    ]),
    wk("2026-09-05T16:00:00+00:00", "Glutei", [
        ex("Stacco da Terra Rumeno (Manubrio)", [s(4, 12, "warmup")] + [s(6, 12)] * 3),
        ex("Step Up Manubrio", [s(0, 10), s(0, 10), s(3, 10), s(3, 10), s(3, 10), s(3, 10), s(3, 10), s(3, 10)]),
        ex("Abduzione Anche (Macchina)", [s(15, 15)] * 2 + [s(17.5, 13, "failure")]),
    ]),
    wk("2026-09-08T16:00:00+00:00", "Giorno 1", [
        ex("Chest Press (Macchina)", [s(12.5, 12)] + [s(20, 12)] * 4),
        ex("Panca Inclinata (Manubrio)", [s(5, 11), s(5, 10), s(5, 10)]),
        ex("Pushdown Tricipiti", [s(7.5, 12, "warmup"), s(10, 12), s(10, 12), s(10, 12, "dropset")]),
    ]),
]

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixture_workouts.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump({"note": "dati inventati per i test", "workouts": W}, f, indent=1, ensure_ascii=False)
print(path)
