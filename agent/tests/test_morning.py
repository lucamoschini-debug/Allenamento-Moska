"""Test con dati inventati: python -m unittest discover -s agent/tests"""
import datetime as dt
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import morning  # noqa: E402

D = dt.date


def s(w, r, t="normal"):
    return {"type": t, "weight_kg": w, "reps": r}


def wk(start, exercises, title="x"):
    return {"title": title, "start_time": start, "exercises": [{"title": t, "sets": ss} for t, ss in exercises]}


SCHEDA = morning.load_scheda()


def ex_cfg(session, name):
    return next(e for e in SCHEDA["sessions"][session]["exercises"] if e["name"] == name)


UPPER_A = [("Chest Press (Macchina)", [s(20, 12)] * 4), ("Croci (Macchina)", [s(5, 12)] * 3)]
LOWER_A = [("Leg Press Orizzontale (Macchina)", [s(40, 10)] * 4), ("Leg Extension (Macchina)", [s(10, 12)] * 3)]
UPPER_B = [("Trazione verticale (macchina)", [s(25, 12)] * 4), ("Curl Bicipiti (Manubrio)", [s(9, 12)] * 3)]
UPPER_C = [("Alzate Laterali (Macchina)", [s(10, 12)] * 3), ("Macchina Spalle Ignota", [s(5, 10)] * 4)]
LOWER_B = [("Hip Thrust (macchina)", [s(30, 12)] * 3), ("Step Up Manubrio", [s(3, 12)] * 6)]


class TestTime(unittest.TestCase):
    def test_utc_to_rome_dst(self):
        self.assertEqual(morning.to_rome("2026-10-10T22:30:00+00:00").date(), D(2026, 10, 11))
        self.assertEqual(morning.to_rome("2026-11-10T23:30:00Z").date(), D(2026, 11, 11))
        self.assertEqual(morning.to_rome("2026-11-10T22:30:00Z").date(), D(2026, 11, 10))


class TestClassify(unittest.TestCase):
    def test_by_exercises_not_title(self):
        ws = [wk("2026-09-01T10:00:00Z", UPPER_B, title="Gambe (titolo sbagliato)")]
        self.assertEqual(morning.classify(ws, SCHEDA)[0][1], "Upper B")

    def test_legacy_titles(self):
        ws = [wk("2026-09-01T10:00:00Z", [("Pushdown Tricipiti con Corda", [s(5, 12)])])]
        self.assertEqual(morning.classify(ws, SCHEDA)[0][1], "Upper A")
        ws = [wk("2026-09-01T10:00:00Z", [("Concentration Curl", [s(5, 12)])])]
        self.assertEqual(morning.classify(ws, SCHEDA)[0][1], "Upper B")

    def test_upper_c_tie_uses_rotation(self):
        ws = [wk("2026-09-01T10:00:00Z", UPPER_B), wk("2026-09-02T10:00:00Z", UPPER_C)]
        self.assertEqual([l for _, l in morning.classify(ws, SCHEDA)], ["Upper B", "Upper C"])

    def test_core_workout_ignored(self):
        ws = [wk("2026-09-01T10:00:00Z", UPPER_A), wk("2026-09-02T10:00:00Z", [("Dead Bug", [s(None, 8)])], title="Core Completo")]
        cl = morning.classify(ws, SCHEDA)
        self.assertEqual([l for _, l in cl], ["Upper A"])
        self.assertNotIn("Dead Bug", morning.build_message(cl, SCHEDA, D(2026, 9, 3)))
        ws = [wk("2026-09-01T10:00:00Z", LOWER_A, title="Giorno 2 - Lower A - Quadricipiti e Core")]
        self.assertEqual(morning.classify(ws, SCHEDA)[0][1], "Lower A")

    def test_walk_only_ignored(self):
        ws = [wk("2026-09-01T10:00:00Z", [("Camminata", [{"type": "normal", "distance_meters": 900}])])]
        self.assertIsNone(morning.classify(ws, SCHEDA)[0][1])


class TestPlan(unittest.TestCase):
    def plan(self, workouts, today):
        return morning.plan_day(morning.classify(workouts, SCHEDA), SCHEDA, today)

    def test_rotation_next(self):
        p = self.plan([wk("2026-09-01T10:00:00Z", UPPER_A)], D(2026, 9, 3))
        self.assertEqual((p["mode"], p["session"]), ("train", "Lower A"))
        p = self.plan([wk("2026-09-01T10:00:00Z", UPPER_B)], D(2026, 9, 2))
        self.assertEqual(p["session"], "Upper C")

    def test_rest_after_lower_b(self):
        ws = [wk("2026-10-10T12:00:00Z", LOWER_B)]
        p = self.plan(ws, D(2026, 10, 11))
        self.assertEqual((p["mode"], p["session"]), ("rest", "Upper A"))
        p = self.plan(ws, D(2026, 10, 12))
        self.assertEqual((p["mode"], p["session"]), ("train", "Upper A"))

    def test_rest_already_taken_before_lower_b(self):
        # Upper C il 16, riposo il 17-18, Lower B il 19: il 20 si allena.
        ws = [wk("2026-10-16T10:00:00Z", UPPER_C), wk("2026-10-19T10:00:00Z", LOWER_B)]
        p = self.plan(ws, D(2026, 10, 20))
        self.assertEqual((p["mode"], p["session"]), ("train", "Upper A"))
        m = morning.build_message(morning.classify(ws, SCHEDA), SCHEDA, D(2026, 10, 20))
        self.assertIn("Oggi: Upper A", m)
        self.assertIn("Riposo già fatto prima del Lower B (allenamento precedente: 16/10).", m)
        p = self.plan(ws, D(2026, 10, 19))
        self.assertEqual(p["mode"], "done")
        self.assertNotIn("1-2 giorni di riposo", morning.build_message(morning.classify(ws, SCHEDA), SCHEDA, D(2026, 10, 19)))

    def test_rest_needed_when_lower_b_follows_without_rest(self):
        ws = [wk("2026-10-18T10:00:00Z", UPPER_C), wk("2026-10-19T10:00:00Z", LOWER_B)]
        self.assertEqual(self.plan(ws, D(2026, 10, 20))["mode"], "rest")

    def test_core_day_does_not_break_rest_check(self):
        # Il Core non è un allenamento della rotazione: il giorno di Core conta come riposo.
        ws = [wk("2026-10-16T10:00:00Z", UPPER_C), wk("2026-10-17T10:00:00Z", [("Dead Bug", [s(None, 8)])], title="Core Completo"),
              wk("2026-10-19T10:00:00Z", LOWER_B)]
        self.assertEqual(self.plan(ws, D(2026, 10, 20))["mode"], "train")

    def test_already_trained_today(self):
        p = self.plan([wk("2026-09-01T06:00:00Z", UPPER_A)], D(2026, 9, 1))
        self.assertEqual((p["mode"], p["next"]), ("done", "Lower A"))

    def test_no_history_starts_rotation(self):
        p = self.plan([], D(2026, 9, 1))
        self.assertEqual(p["session"], "Upper A")


class TestSets(unittest.TestCase):
    def test_marked_warmup_excluded(self):
        ws = morning.working_sets([s(5, 12, "warmup"), s(10, 12), s(10, 12, "failure"), s(8, 12, "dropset")])
        self.assertEqual([x["weight_kg"] for x in ws], [10, 10, 8])

    def test_implicit_warmup(self):
        self.assertEqual(len(morning.working_sets([s(8, 12), s(10, 12), s(10, 12)])), 2)  # 8 < 8.5
        self.assertEqual(len(morning.working_sets([s(9, 12), s(10, 12), s(10, 12)])), 3)  # 9 >= 8.5

    def test_step_up_grouping(self):
        g = morning.group_per_leg([s(0, 10), s(0, 10), s(3, 10), s(3, 10), s(4, 10), s(4, 9)])
        self.assertEqual([(x["weight_kg"], x["reps"]) for x in g], [(0, 10), (3, 10), (4, 9)])
        self.assertEqual(len(morning.working_sets(g)), 2)


class TestSuggest(unittest.TestCase):
    def sug(self, session, name, workouts, today=D(2026, 10, 5)):
        cfg = ex_cfg(session, name)
        cl = morning.classify(workouts, SCHEDA)
        hist = morning.history_for(cfg, cl, SCHEDA)
        return morning.suggest(cfg, hist, morning.phase(SCHEDA, today), 0.9)

    def test_progress_after_two_sessions_at_top(self):
        ws = [wk("2026-09-01T10:00:00Z", UPPER_B), wk("2026-09-05T10:00:00Z", UPPER_B)]
        self.assertTrue(self.sug("Upper B", "Trazione verticale (macchina)", ws)[0].startswith("27,5 kg"))
        self.assertEqual(self.sug("Upper B", "Curl Bicipiti (Manubrio)", ws)[0], "10 kg per mano")

    def test_no_progress_if_load_changed(self):
        ws = [wk("2026-09-01T10:00:00Z", [("Rematore Seduto (Macchina)", [s(12.5, 12)] * 3)]),
              wk("2026-09-05T10:00:00Z", [("Rematore Seduto (Macchina)", [s(12.5, 12), s(12.5, 12), s(15, 12)])])]
        self.assertEqual(self.sug("Upper B", "Rematore Seduto (Macchina)", ws)[0], "12,5/12,5/15 kg")

    def test_one_session_at_top_keeps_load(self):
        load, goal, _, _ = self.sug("Upper B", "Trazione verticale (macchina)", [wk("2026-09-01T10:00:00Z", UPPER_B)])
        self.assertEqual(load, "25 kg")
        self.assertIn("già al tetto", goal)

    def test_below_top_same_load_more_reps(self):
        load, goal, last, _ = self.sug("Lower A", "Leg Press Orizzontale (Macchina)", [wk("2026-09-01T10:00:00Z", LOWER_A)])
        self.assertEqual(load, "40 kg")
        self.assertIn("+1-2 rip", goal)
        self.assertIn("4x10 a 40 kg", last)

    def test_step_up_top_is_12(self):
        ws = [wk("2026-09-01T10:00:00Z", LOWER_B), wk("2026-09-06T10:00:00Z", LOWER_B)]
        self.assertEqual(self.sug("Lower B", "Step Up Manubrio", ws)[0], "4 kg per mano (in Hevy 8 kg totali)")

    def test_no_history(self):
        self.assertEqual(self.sug("Upper C", "Tirata verticale a presa neutra", [])[0], morning.TARATURA)
        self.assertIn("partenza 7,5 kg", self.sug("Upper C", "Croci Inverse Deltoide Posteriore (Macchina)", [])[0])
        self.assertEqual(self.sug("Lower A", "Back Extension (Iperestensione)", [])[0], "corpo libero")

    def test_deload_minus_10_percent(self):
        ws = [wk("2026-10-20T10:00:00Z", UPPER_B), wk("2026-10-22T10:00:00Z", UPPER_B)]
        load, goal, _, _ = self.sug("Upper B", "Trazione verticale (macchina)", ws, today=D(2026, 10, 27))
        self.assertEqual(load, "22,5 kg")  # 25*0.9=22.5, nessuna progressione
        self.assertIn("RPE 6", goal)

    def test_after_cycle_no_progression(self):
        ws = [wk("2026-10-20T10:00:00Z", UPPER_B), wk("2026-10-22T10:00:00Z", UPPER_B)]
        self.assertEqual(self.sug("Upper B", "Trazione verticale (macchina)", ws, today=D(2026, 12, 1))[0], "25 kg")

    def test_new_scheda_start_load_wins_once(self):
        cfg = ex_cfg("Upper B", "Curl Bicipiti (Manubrio)")
        vdt = morning.dt.datetime.fromisoformat("2026-10-11T23:20:00+02:00")
        old = [wk("2026-10-04T10:00:00Z", UPPER_B), wk("2026-10-09T10:00:00Z", UPPER_B)]
        hist = morning.history_for(cfg, morning.classify(old, SCHEDA), SCHEDA)
        self.assertEqual(morning.suggest(cfg, hist, "normal", 0.9, vdt)[0], "8 kg per mano")  # Excel, non 10
        new = old + [wk("2026-10-15T10:00:00Z", UPPER_B)]
        hist = morning.history_for(cfg, morning.classify(new, SCHEDA), SCHEDA)
        self.assertEqual(morning.suggest(cfg, hist, "normal", 0.9, vdt)[0], "10 kg per mano")  # poi la regola

    def test_plates_unit(self):
        self.assertEqual(morning.fmt_loads([15, 15, 15], "kg di dischi"), "15 kg di dischi (7,5 per lato)")

    def test_bodyweight_at_top(self):
        cfg = ex_cfg("Lower A", "Back Extension (Iperestensione)")
        ws = [wk("2026-09-01T10:00:00Z", [("Back Extension (Iperestensione)", [s(None, 12)] * 3)])]
        hist = morning.history_for(cfg, morning.classify(ws, SCHEDA), SCHEDA)
        self.assertIn("zavorra", morning.suggest(cfg, hist, "normal", 0.9)[1])

    def test_hevy_total_of_two_dumbbells(self):
        cfg = ex_cfg("Lower B", "Stacco da Terra Rumeno (Manubrio)")
        ws = [wk("2026-10-10T10:00:00Z", [("Stacco da Terra Rumeno (Manubrio)", [s(8, 12)] * 3)]),   # per mano
              wk("2026-10-19T10:00:00Z", [("Stacco da Terra Rumeno (Manubrio)", [s(16, 12)] * 3)])]  # totale
        hist = morning.history_for(cfg, morning.classify(ws, SCHEDA), SCHEDA)
        load, goal, last, _ = morning.suggest(cfg, hist, "normal", 0.9)
        self.assertEqual(load, "9 kg per mano (in Hevy 18 kg totali)")  # 2 sedute al tetto a 8 per mano
        self.assertIn("a 8 kg", last)

    def test_weeks_of_v15_cycle(self):
        self.assertEqual(morning.phase(SCHEDA, D(2026, 10, 20)), "normal")      # S3
        self.assertEqual(morning.phase(SCHEDA, D(2026, 10, 28)), "deload")      # S4
        self.assertEqual(morning.phase(SCHEDA, D(2026, 11, 4)), "normal")       # S5
        self.assertEqual(morning.phase(SCHEDA, D(2026, 11, 10)), "no_progression")  # S6
        self.assertEqual(morning.phase(SCHEDA, D(2026, 11, 18)), "normal")      # S7
        self.assertEqual(morning.phase(SCHEDA, D(2026, 11, 25)), "deload")      # S8
        self.assertEqual(morning.phase(SCHEDA, D(2026, 11, 30)), "ended")

    def test_no_progression_week_s6(self):
        ws = [wk("2026-11-03T10:00:00Z", UPPER_B), wk("2026-11-06T10:00:00Z", UPPER_B)]
        load, goal, _, _ = self.sug("Upper B", "Trazione verticale (macchina)", ws, today=D(2026, 11, 10))
        self.assertEqual(load, "25 kg")
        self.assertIn("settimana di ripetizioni", goal)

    def test_restart_after_deload_uses_pre_deload_loads(self):
        before = [("Trazione verticale (macchina)", [s(30, 11)] * 4)]
        during = [("Trazione verticale (macchina)", [s(27.5, 12)] * 4)]
        ws = [wk("2026-10-22T10:00:00Z", before), wk("2026-10-29T10:00:00Z", during)]
        self.assertEqual(self.sug("Upper B", "Trazione verticale (macchina)", ws, today=D(2026, 11, 3))[0], "30 kg")

    def test_chest_press_progression_suspended(self):
        a = [("Chest Press (Macchina)", [s(17.5, 12)] * 3)]
        ws = [wk("2026-10-21T10:00:00Z", a), wk("2026-10-24T10:00:00Z", a)]
        load, goal, _, _ = self.sug("Upper A", "Chest Press (Macchina)", ws, today=D(2026, 10, 25))
        self.assertEqual(load, "17,5 kg")
        self.assertIn("progressione sospesa", goal)

    def test_round_step(self):
        self.assertEqual(morning.deload(17.5, 2.5, 0.9), 15)
        self.assertEqual(morning.deload(7, 1, 0.9), 6)
        self.assertEqual(morning.deload(50, 5, 0.9), 45)


class TestMessage(unittest.TestCase):
    def msg(self, workouts, today):
        return morning.build_message(morning.classify(workouts, SCHEDA), SCHEDA, today)

    def test_rest_message(self):
        m = self.msg([wk("2026-10-10T12:00:00Z", LOWER_B)], D(2026, 10, 11))
        self.assertIn("Oggi riposo consigliato", m)
        self.assertIn("Domani: Upper A", m)
        self.assertIn("Core o Mobilità e cervicale, se hai tempo.", m)

    def test_training_message_and_core(self):
        m = self.msg([wk("2026-10-10T12:00:00Z", LOWER_B)], D(2026, 10, 12))
        self.assertIn("Oggi: Upper A (circa 62-68 min)", m)
        self.assertLess(len(m), 3500)

    def test_no_walk_references_anywhere(self):
        """Il Cammino è in pausa: nessun riferimento alle camminate, in nessun giorno."""
        banned = ("cammin", " km", "nessuna camminata in piano", "18:30", "09:00")
        cases = [
            ([wk("2026-10-12T12:00:00Z", UPPER_A)], D(2026, 10, 14)),   # mercoledì, allenamento
            ([wk("2026-10-17T12:00:00Z", LOWER_B)], D(2026, 10, 18)),   # domenica, riposo
            ([wk("2026-10-20T12:00:00Z", UPPER_B)], D(2026, 10, 21)),
            ([wk("2026-10-24T12:00:00Z", LOWER_B)], D(2026, 10, 25)),
            ([wk("2026-10-27T12:00:00Z", UPPER_A)], D(2026, 10, 28)),   # scarico
            ([wk("2026-10-31T12:00:00Z", LOWER_B)], D(2026, 11, 1)),
            ([wk("2026-11-02T12:00:00Z", UPPER_C)], D(2026, 11, 4)),    # dopo il ciclo
            ([wk("2026-10-14T05:00:00Z", UPPER_A)], D(2026, 10, 14)),   # già allenato oggi
        ]
        for workouts, today in cases:
            m = self.msg(workouts, today).lower()
            for b in banned:
                self.assertNotIn(b, m, "%s in messaggio del %s" % (b, today))
        self.assertNotIn("walks", SCHEDA)

    def test_hevy_walk_not_listed_as_unrecognized(self):
        ws = [wk("2026-10-13T12:00:00Z", UPPER_A + [("Camminata", [{"type": "normal", "distance_meters": 900}])])]
        self.assertNotIn("Camminata", self.msg(ws, D(2026, 10, 14)))

    def test_no_walks_no_core_on_training_days(self):
        m = self.msg([wk("2026-10-12T12:00:00Z", UPPER_A)], D(2026, 10, 14))
        self.assertNotIn("Camminata", m)
        self.assertNotIn("Core", m)

    def test_cycle_end(self):
        m = self.msg([wk("2026-11-28T12:00:00Z", UPPER_A)], D(2026, 12, 1))
        self.assertIn("serve la nuova scheda", m)
        m = self.msg([wk("2026-11-01T12:00:00Z", UPPER_A)], D(2026, 11, 3))
        self.assertNotIn("serve la nuova scheda", m)

    def test_deload_note(self):
        m = self.msg([wk("2026-10-26T12:00:00Z", UPPER_A)], D(2026, 10, 28))
        self.assertIn("Settimana di scarico", m)

    def test_upper_c_new_exercises(self):
        m = self.msg([wk("2026-09-01T10:00:00Z", UPPER_B)], D(2026, 9, 2))
        self.assertIn("Oggi: Upper C", m)
        self.assertIn("Lento in Avanti Seduto (Macchina) — SOSPESO fino alla visita dal fisioterapista (se lo reintroduci: 3x10-12 da 15 kg)", m)
        self.assertIn("Tirata verticale a presa neutra — 3x10-12 — taratura", m)

    def test_upright_row_in_upper_c(self):
        ws = [wk("2026-10-16T10:00:00Z", [("Rematore Verticale (Bilanciere)", [s(15, 12)] * 4)] + UPPER_C)]
        m = self.msg(ws, D(2026, 10, 21))
        self.assertNotIn("non riconosciuti: Rematore Verticale", m)

    def test_skipped_session_signalled(self):
        ws = [wk("2026-10-05T10:00:00Z", LOWER_A), wk("2026-10-11T10:00:00Z", UPPER_A),
              wk("2026-10-13T10:00:00Z", UPPER_B), wk("2026-10-16T10:00:00Z", UPPER_C),
              wk("2026-10-19T10:00:00Z", LOWER_B)]
        cl = morning.classify(ws, SCHEDA)
        self.assertEqual(morning.skipped_sessions(cl, SCHEDA), [("Lower A", D(2026, 10, 5))])
        self.assertIn("Seduta saltata nella rotazione: Lower A (ultima volta 05/10).", morning.build_message(cl, SCHEDA, D(2026, 10, 21)))
        # Recuperata: Upper A seguito da Lower A, niente più segnalazione.
        ws += [wk("2026-10-21T10:00:00Z", UPPER_A), wk("2026-10-22T10:00:00Z", LOWER_A)]
        self.assertEqual(morning.skipped_sessions(morning.classify(ws, SCHEDA), SCHEDA), [])

    def test_rope_pushdown_is_an_exercise_again(self):
        names = [e["name"] for e in SCHEDA["sessions"]["Upper A"]["exercises"]]
        self.assertIn("Pushdown Tricipiti con Corda", names)
        self.assertNotIn("Estensione Tricipiti Sopra la Testa (Cavo)", names)
        self.assertNotIn("Rematore Verticale (Bilanciere)", [e["name"] for e in SCHEDA["sessions"]["Upper C"]["exercises"]])

    def test_unrecognized_listed(self):
        m = self.msg([wk("2026-09-01T10:00:00Z", UPPER_C)], D(2026, 9, 3))
        self.assertIn("Esercizi non riconosciuti: Macchina Spalle Ignota", m)

    def test_fixture_end_to_end(self):
        with open(os.path.join(HERE, "fixture_workouts.json"), encoding="utf-8") as f:
            ws = json.load(f)["workouts"]
        m = self.msg(ws, D(2026, 9, 9))
        self.assertIn("Oggi: Lower A", m)
        self.assertNotIn("Camminata,", m)


def wkt(start, exercises, title="x"):
    """Allenamento con codici esercizio Hevy (necessari per il confronto con le routine)."""
    return {"title": title, "start_time": start,
            "exercises": [{"title": n, "exercise_template_id": tid, "sets": ss} for n, tid, ss in exercises]}


def routine(title, exercises):
    return {"title": title, "exercises": [{"title": n, "exercise_template_id": tid, "sets": ss} for n, tid, ss in exercises]}


class TestRoutines(unittest.TestCase):
    """Routine Hevy inventate: nomi inglesi, collegate agli allenamenti dal codice esercizio."""
    W = s(5, 5, "warmup")

    def block(self, workouts, routines, session="Upper A", today=D(2026, 10, 21), ph="normal"):
        cl = morning.classify(workouts, SCHEDA)
        return morning.session_block(session, cl, SCHEDA, today, ph, routines)

    def test_routine_load_wins_and_difference_is_reported(self):
        ws = [wkt("2026-10-11T10:00:00Z", [("Chest Press (Macchina)", "T1", [s(17.5, 12)] * 3)])]
        rt = [routine("Giorno 1 - Upper A - Petto", [("Chest Press (Machine)", "T1", [self.W] + [s(20, 12)] * 3)])]
        lines, _, _, diffs = self.block(ws, rt)
        self.assertIn("Chest Press (Macchina) — 3x10-12 — 20 kg", lines[0])
        self.assertIn("Chest Press (Macchina) 3x 20 kg (scheda 3x 17,5 kg)", diffs)
        self.assertIn("Pushdown Tricipiti con Corda manca nella routine", diffs)

    def test_progression_on_top_of_routine_load(self):
        a = [("Trazione verticale (macchina)", "T2", [s(30, 12)] * 4)]
        ws = [wkt("2026-10-20T10:00:00Z", a), wkt("2026-10-23T10:00:00Z", a)]
        rt = [routine("Giorno 3 - Upper B", [("Vertical Traction (Machine)", "T2", [self.W] + [s(30, 12)] * 4)])]
        lines, _, _, diffs = self.block(ws, rt, "Upper B", D(2026, 10, 24))
        self.assertIn("Trazione verticale (macchina) — 4x10-12 — 32,5 kg (aumento maturo (2 sedute al tetto): porta la routine Hevy a 32,5 kg", lines[0])
        self.assertFalse([d for d in diffs if d.startswith("Trazione")])  # stesso risultato della scheda

    def test_no_progression_if_done_below_routine_load(self):
        a = [("Trazione verticale (macchina)", "T2", [s(27.5, 12)] * 4)]
        ws = [wkt("2026-10-20T10:00:00Z", a), wkt("2026-10-23T10:00:00Z", a)]
        rt = [routine("Giorno 3 - Upper B", [("Vertical Traction (Machine)", "T2", [s(30, 12)] * 4)])]
        lines, _, _, _ = self.block(ws, rt, "Upper B", D(2026, 10, 24))
        self.assertIn("— 30 kg (stesso carico, +1-2 rip", lines[0])

    def test_routine_sets_count_wins(self):
        ws = [wkt("2026-10-16T10:00:00Z", [("Face Pull", "T3", [s(10, 15)] * 3)])]
        rt = [routine("Giorno 4 - Upper C", [("Face Pull", "T3", [s(10, 15)] * 2)])]
        lines, _, _, diffs = self.block(ws, rt, "Upper C")
        self.assertTrue(any(l.startswith("- Face Pull — 2x12-15 — 10 kg") for l in lines))
        self.assertTrue(any(d.startswith("Face Pull 2x 10 kg (scheda 3x") for d in diffs))

    def test_extra_and_suspended_exercises_reported(self):
        ws = [wkt("2026-10-16T10:00:00Z", [("Lento in Avanti Seduto (Macchina)", "T4", [s(15, 12)] * 3),
                                           ("Rematore Verticale (Bilanciere)", "T5", [s(15, 12)] * 3)])]
        rt = [routine("Giorno 4 - Upper C", [("Seated Shoulder Press (Machine)", "T4", [s(15, 12)] * 3),
                                              ("Upright Row (Barbell)", "T5", [s(15, 12)] * 3)])]
        _, _, _, diffs = self.block(ws, rt, "Upper C")
        self.assertIn("Lento in Avanti Seduto (Macchina) è sospeso ma è ancora nella routine", diffs)
        self.assertIn("Rematore Verticale (Bilanciere) è nella routine ma non in scheda", diffs)

    def test_dumbbell_total_and_per_leg_in_routine(self):
        ws = [wkt("2026-10-19T10:00:00Z", [("Step Up Manubrio", "T6", [s(14, 12)] * 6)])]
        rt = [routine("Giorno 5 - Lower B", [("Dumbbell Step Up", "T6", [s(10, 10, "warmup")] * 2 + [s(14, 12)] * 6)])]
        lines, _, _, _ = self.block(ws, rt, "Lower B")
        line = [l for l in lines if l.startswith("- Step Up")][0]
        self.assertIn("— 3x10-12 per gamba — 7 kg per mano (in Hevy 14 kg totali)", line)

    def test_deload_applies_to_routine_load(self):
        ws = [wkt("2026-10-22T10:00:00Z", [("Chest Press (Macchina)", "T1", [s(20, 12)] * 3)])]
        rt = [routine("Giorno 1 - Upper A", [("Chest Press (Machine)", "T1", [s(20, 12)] * 3)])]
        lines, _, _, _ = self.block(ws, rt, today=D(2026, 10, 28), ph="deload")
        self.assertIn("— 17,5 kg (scarico, RPE 6", lines[0])

    def test_template_id_from_scheda_and_taratura_not_a_difference(self):
        rt = [routine("Giorno 3 - Upper B", [("Single Arm Cable Row", "D0C4A899", [s(10, 12)] * 3)])]
        lines, _, _, diffs = self.block([], rt, "Upper B")
        self.assertTrue(any(l.startswith("- Rematore al Cavo a Braccio Singolo — 3x10-12 — 10 kg") for l in lines))
        self.assertFalse([d for d in diffs if "Braccio" in d or "Single Arm" in d])

    def test_routine_updated_no_double_increase(self):
        old = [("Trazione verticale (macchina)", "T2", [s(30, 12)] * 4)]
        ws = [wkt("2026-10-20T10:00:00Z", old), wkt("2026-10-23T10:00:00Z", old)]
        rt = [routine("Giorno 3 - Upper B", [("Vertical Traction (Machine)", "T2", [s(32.5, 12)] * 4)])]
        lines, _, _, _ = self.block(ws, rt, "Upper B", D(2026, 10, 24))
        self.assertIn("— 32,5 kg (stesso carico, +1-2 rip", lines[0])

    def test_increase_done_but_routine_not_updated(self):
        ws = [wkt("2026-10-20T10:00:00Z", [("Trazione verticale (macchina)", "T2", [s(30, 12)] * 4)]),
              wkt("2026-10-23T10:00:00Z", [("Trazione verticale (macchina)", "T2", [s(32.5, 10)] * 4)])]
        rt = [routine("Giorno 3 - Upper B", [("Vertical Traction (Machine)", "T2", [s(30, 12)] * 4)])]
        lines, _, _, _ = self.block(ws, rt, "Upper B", D(2026, 10, 24))
        self.assertIn("— 32,5 kg (routine Hevy non aggiornata: portala a 32,5 kg", lines[0])

    def test_increase_in_hevy_units_for_dumbbell_totals(self):
        a = [("Stacco da Terra Rumeno (Manubrio)", "T8", [s(16, 12)] * 3)]
        ws = [wkt("2026-10-19T10:00:00Z", a), wkt("2026-10-22T10:00:00Z", a)]
        rt = [routine("Giorno 5 - Lower B", [("Romanian Deadlift (Dumbbell)", "T8", [s(16, 12)] * 3)])]
        lines, _, _, _ = self.block(ws, rt, "Lower B", D(2026, 10, 24))
        self.assertIn("9 kg per mano (in Hevy 18 kg totali) (aumento maturo (2 sedute al tetto): porta la routine Hevy a 18 kg", lines[0])

    def test_stale_check_only_within_same_routine(self):
        # 12,5 kg fatti nell'Upper C non rendono "non aggiornata" la routine Upper A a 10 kg.
        ws = [wkt("2026-10-20T10:00:00Z", [("Chest Press (Macchina)", "T1", [s(17.5, 12)] * 3),
                                           ("Alzate Laterali (Macchina)", "T9", [s(10, 12)] * 3)]),
              wkt("2026-10-24T10:00:00Z", [("Face Pull", "T3", [s(10, 15)] * 3),
                                           ("Alzate Laterali (Macchina)", "T9", [s(12.5, 12)] * 3)])]
        rt = [routine("Giorno 1 - Upper A", [("Lateral Raise (Machine)", "T9", [s(10, 12)] * 3)])]
        lines, _, _, _ = self.block(ws, rt, "Upper A", D(2026, 10, 25))
        line = [l for l in lines if l.startswith("- Alzate")][0]
        self.assertIn("— 10 kg (stesso carico", line)
        self.assertNotIn("non aggiornata", line)

    def test_walk_in_routine_ignored(self):
        ws = [wkt("2026-10-04T10:00:00Z", [("Camminata", "W1", [{"type": "normal", "distance_meters": 900}])])]
        rt = [routine("Giorno 2 - Lower A", [("Walking", "W1", [{"type": "normal"}])])]
        _, _, _, diffs = self.block(ws, rt, "Lower A")
        self.assertFalse([d for d in diffs if "Camminata" in d or "Walking" in d])

    def test_without_routines_scheda_only(self):
        ws = [wkt("2026-10-11T10:00:00Z", [("Chest Press (Macchina)", "T1", [s(17.5, 12)] * 3)])]
        lines, _, _, diffs = self.block(ws, None)
        self.assertIn("— 17,5 kg", lines[0])
        self.assertEqual(diffs, [])

    def test_message_note(self):
        ws = [wkt("2026-10-19T10:00:00Z", [("Hip Thrust (macchina)", "T7", [s(35, 12)] * 4)])]
        rt = [routine("Giorno 1 - Upper A", [("Chest Press (Machine)", "T1", [s(20, 12)] * 3)])]
        m = morning.build_message(morning.classify(ws, SCHEDA), SCHEDA, D(2026, 10, 21), rt)
        self.assertIn("Differenze con la routine Hevy (vale Hevy):", m)
        self.assertLess(len(m), 3500)


class TestSafety(unittest.TestCase):
    def test_export_only_reads_hevy(self):
        with open(os.path.join(os.path.dirname(HERE), "export_coach.py"), encoding="utf-8") as f:
            src = f.read()
        for bad in ("urllib", "requests", "method=", "data=", "telegram"):
            self.assertNotIn(bad, src)

    def test_host_allowlist(self):
        with self.assertRaises(morning.FetchError):
            morning._check_host("https://example.com/x")

    def test_only_get_to_hevy(self):
        with open(morning.__file__, encoding="utf-8") as f:
            src = f.read()
        for verb in ('method="PUT"', 'method="DELETE"', 'method="PATCH"'):
            self.assertNotIn(verb, src)
        hevy_fn = src.split("def hevy_get")[1].split("def fetch_workouts")[0]
        self.assertNotIn("data=", src.split("def fetch_routines")[1].split("def telegram_send")[0])
        self.assertIn('method="GET"', hevy_fn)
        self.assertNotIn("data=", hevy_fn)


if __name__ == "__main__":
    unittest.main()
