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
LOWER_B = [("Hip Thrust (macchina)", [s(30, 12)] * 3), ("Step Up Manubrio", [s(3, 10)] * 6)]


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

    def test_step_up_top_is_10(self):
        ws = [wk("2026-09-01T10:00:00Z", LOWER_B), wk("2026-09-06T10:00:00Z", LOWER_B)]
        self.assertEqual(self.sug("Lower B", "Step Up Manubrio", ws)[0], "4 kg per mano")

    def test_no_history(self):
        self.assertEqual(self.sug("Upper C", "Face Pull", [])[0], morning.TARATURA)
        self.assertIn("partenza 5 kg", self.sug("Upper C", "Lento in Avanti Seduto (Macchina)", [])[0])
        self.assertEqual(self.sug("Lower A", "Back Extension (Iperestensione)", [])[0], "corpo libero")

    def test_deload_minus_10_percent(self):
        ws = [wk("2026-10-20T10:00:00Z", UPPER_B), wk("2026-10-22T10:00:00Z", UPPER_B)]
        load, goal, _, _ = self.sug("Upper B", "Trazione verticale (macchina)", ws, today=D(2026, 10, 27))
        self.assertEqual(load, "22,5 kg")  # 25*0.9=22.5, nessuna progressione
        self.assertIn("RPE 6", goal)

    def test_after_cycle_no_progression(self):
        ws = [wk("2026-10-20T10:00:00Z", UPPER_B), wk("2026-10-22T10:00:00Z", UPPER_B)]
        self.assertEqual(self.sug("Upper B", "Trazione verticale (macchina)", ws, today=D(2026, 11, 3))[0], "25 kg")

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
        self.assertIn("Oggi: Upper A (circa 68-72 min)", m)
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
        m = self.msg([wk("2026-11-01T12:00:00Z", UPPER_A)], D(2026, 11, 3))
        self.assertIn("serve la nuova scheda", m)

    def test_deload_note(self):
        m = self.msg([wk("2026-10-26T12:00:00Z", UPPER_A)], D(2026, 10, 28))
        self.assertIn("Settimana di scarico", m)

    def test_upper_c_new_exercises(self):
        m = self.msg([wk("2026-09-01T10:00:00Z", UPPER_B)], D(2026, 9, 2))
        self.assertIn("Oggi: Upper C", m)
        self.assertIn("Nuovi esercizi (senza storico): Lento in Avanti Seduto (Macchina)", m)

    def test_unrecognized_listed(self):
        m = self.msg([wk("2026-09-01T10:00:00Z", UPPER_C)], D(2026, 9, 3))
        self.assertIn("Esercizi non riconosciuti: Macchina Spalle Ignota", m)

    def test_fixture_end_to_end(self):
        with open(os.path.join(HERE, "fixture_workouts.json"), encoding="utf-8") as f:
            ws = json.load(f)["workouts"]
        m = self.msg(ws, D(2026, 9, 9))
        self.assertIn("Oggi: Lower A", m)
        self.assertNotIn("Camminata,", m)


class TestSafety(unittest.TestCase):
    def test_host_allowlist(self):
        with self.assertRaises(morning.FetchError):
            morning._check_host("https://example.com/x")

    def test_only_get_to_hevy(self):
        with open(morning.__file__, encoding="utf-8") as f:
            src = f.read()
        for verb in ('method="PUT"', 'method="DELETE"', 'method="PATCH"'):
            self.assertNotIn(verb, src)
        hevy_fn = src.split("def hevy_get")[1].split("def fetch_workouts")[0]
        self.assertIn('method="GET"', hevy_fn)
        self.assertNotIn("data=", hevy_fn)


if __name__ == "__main__":
    unittest.main()
