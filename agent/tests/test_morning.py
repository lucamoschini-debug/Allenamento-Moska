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
    """Calcolo dei carichi con la sola scheda (senza routine Hevy). Date fuori dal ciclo
    (settembre) = settimana normale; dal 26/10 valgono le settimane della v17."""

    def adv(self, session, name, workouts, today=D(2026, 9, 30), version_dt=None):
        cfg = ex_cfg(session, name)
        hist = morning.history_for(cfg, morning.classify(workouts, SCHEDA), SCHEDA)
        return morning.advise(cfg, hist, morning.phase(SCHEDA, today), 0.9, version_dt)

    def test_step_confirmed_after_two_valid_sessions_at_top(self):
        ws = [wk("2026-09-01T10:00:00Z", UPPER_B), wk("2026-09-05T10:00:00Z", UPPER_B)]
        a = self.adv("Upper B", "Trazione verticale (macchina)", ws)
        self.assertEqual(a["load"], "27,5 kg")
        self.assertEqual(a["goal"], "STEP CONFERMATO: porta la routine a 27,5 kg")
        self.assertEqual(a["update"], "27,5 kg")
        a = self.adv("Upper B", "Curl Bicipiti (Manubrio)", ws)
        self.assertEqual((a["load"], a["update"]), ("10 kg per mano", "10 kg"))

    def test_step_repeated_until_a_session_at_the_new_load(self):
        ws = [wk("2026-09-01T10:00:00Z", UPPER_B), wk("2026-09-05T10:00:00Z", UPPER_B), wk("2026-09-09T10:00:00Z", UPPER_B)]
        self.assertTrue(self.adv("Upper B", "Trazione verticale (macchina)", ws)["goal"].startswith("STEP CONFERMATO"))
        ws.append(wk("2026-09-12T10:00:00Z", [("Trazione verticale (macchina)", [s(27.5, 10)] * 3)]))
        a = self.adv("Upper B", "Trazione verticale (macchina)", ws)
        self.assertEqual(a["goal"], "mantieni 27,5 kg, obiettivo +1-2 ripetizioni")
        self.assertIsNone(a["update"])

    def test_no_step_if_load_changed_between_sessions(self):
        ws = [wk("2026-09-01T10:00:00Z", [("Rematore Seduto (Macchina)", [s(12.5, 12)] * 3)]),
              wk("2026-09-05T10:00:00Z", [("Rematore Seduto (Macchina)", [s(12.5, 12), s(12.5, 12), s(15, 12)])])]
        a = self.adv("Upper B", "Rematore Seduto (Macchina)", ws)
        self.assertEqual(a["load"], "12,5/12,5/15 kg")
        self.assertIsNone(a["update"])

    def test_one_session_at_top_keeps_load(self):
        a = self.adv("Upper B", "Trazione verticale (macchina)", [wk("2026-09-01T10:00:00Z", UPPER_B)])
        self.assertEqual(a["load"], "25 kg")
        self.assertIn("mantieni 25 kg: al tetto", a["goal"])
        self.assertIsNone(a["update"])

    def test_below_top_keep_and_more_reps(self):
        a = self.adv("Lower A", "Leg Press Orizzontale (Macchina)", [wk("2026-09-01T10:00:00Z", LOWER_A)])
        self.assertEqual(a["load"], "40 kg")
        self.assertEqual(a["goal"], "mantieni 40 kg, obiettivo +1-2 ripetizioni")
        self.assertIn("4x10 a 40 kg", a["last"])

    def test_step_up_per_leg_and_hevy_units(self):
        ws = [wk("2026-09-01T10:00:00Z", LOWER_B), wk("2026-09-06T10:00:00Z", LOWER_B)]
        a = self.adv("Lower B", "Step Up Manubrio", ws)
        self.assertEqual(a["load"], "4 kg per mano (in Hevy 8 kg totali)")
        self.assertEqual(a["update"], "8 kg")

    def test_no_history(self):
        self.assertEqual(self.adv("Lower A", "Calf Raise Seduto", [])["load"], morning.TARATURA)
        self.assertIn("partenza 12,5 kg", self.adv("Upper C", "Face Pull", [])["load"])
        self.assertEqual(self.adv("Lower A", "Back Extension (Iperestensione)", [])["load"], "corpo libero")

    def test_weeks_of_v17_cycle(self):
        self.assertEqual(morning.phase(SCHEDA, D(2026, 10, 26)), "normal")          # S4 carichi
        self.assertEqual(morning.phase(SCHEDA, D(2026, 11, 4)), "no_progression")   # S5 ripetizioni
        self.assertEqual(morning.phase(SCHEDA, D(2026, 11, 9)), "deload")           # S6 scarico
        self.assertEqual(morning.phase(SCHEDA, D(2026, 11, 15)), "deload")
        self.assertEqual(morning.phase(SCHEDA, D(2026, 11, 16)), "normal")          # S7 ripartenza
        self.assertEqual(morning.phase(SCHEDA, D(2026, 11, 29)), "normal")          # S8 carichi
        self.assertEqual(morning.phase(SCHEDA, D(2026, 11, 30)), "ended")
        self.assertFalse(morning.in_deload(SCHEDA, D(2026, 10, 28)))                # vecchio scarico tolto

    def test_deload_week_minus_10_percent(self):
        ws = [wk("2026-11-03T10:00:00Z", UPPER_B), wk("2026-11-05T10:00:00Z", UPPER_B)]
        a = self.adv("Upper B", "Trazione verticale (macchina)", ws, today=D(2026, 11, 10))
        self.assertEqual(a["load"], "22,5 kg")  # 25 x 0,9 = 22,5, nessuno step
        self.assertIn("scarico", a["goal"]); self.assertIn("RPE 6", a["goal"])
        self.assertIsNone(a["update"])

    def test_deload_sessions_excluded_from_base_and_last(self):
        ws = [wk("2026-11-06T10:00:00Z", [("Trazione verticale (macchina)", [s(30, 11)] * 3)]),
              wk("2026-11-10T10:00:00Z", [("Trazione verticale (macchina)", [s(27.5, 12)] * 3)])]   # scarico
        a = self.adv("Upper B", "Trazione verticale (macchina)", ws, today=D(2026, 11, 17))
        self.assertEqual(a["load"], "30 kg")
        self.assertIn("ultima volta 06/11", a["last"])
        self.assertIn("scarico del 10/11 escluso", a["last"])

    def test_no_step_when_a_deload_session_is_in_between(self):
        top = [("Trazione verticale (macchina)", [s(30, 12)] * 3)]
        ws = [wk("2026-11-06T10:00:00Z", top),
              wk("2026-11-11T10:00:00Z", [("Trazione verticale (macchina)", [s(27.5, 12)] * 3)]),   # scarico
              wk("2026-11-17T10:00:00Z", top)]
        a = self.adv("Upper B", "Trazione verticale (macchina)", ws, today=D(2026, 11, 18))
        self.assertEqual(a["load"], "30 kg")
        self.assertIsNone(a["update"])
        self.assertIn("al tetto", a["goal"])
        # due sedute valide consecutive dopo lo scarico: lo step scatta
        ws.append(wk("2026-11-19T10:00:00Z", top))
        a = self.adv("Upper B", "Trazione verticale (macchina)", ws, today=D(2026, 11, 20))
        self.assertEqual(a["goal"], "STEP CONFERMATO: porta la routine a 32,5 kg")

    def test_deload_sessions_do_not_count_against(self):
        # una seduta di scarico sotto il tetto non annulla le due sedute valide prima
        top = [("Trazione verticale (macchina)", [s(30, 12)] * 3)]
        ws = [wk("2026-11-03T10:00:00Z", top), wk("2026-11-05T10:00:00Z", top),
              wk("2026-11-12T10:00:00Z", [("Trazione verticale (macchina)", [s(27.5, 8)] * 2)])]   # scarico
        a = self.adv("Upper B", "Trazione verticale (macchina)", ws, today=D(2026, 11, 17))
        self.assertEqual(a["goal"], "STEP CONFERMATO: porta la routine a 32,5 kg")

    def test_cycle_ended_keeps_loads_without_progression(self):
        ws = [wk("2026-11-24T10:00:00Z", UPPER_B), wk("2026-11-26T10:00:00Z", UPPER_B)]
        a = self.adv("Upper B", "Trazione verticale (macchina)", ws, today=D(2026, 12, 1))
        self.assertEqual(a["load"], "25 kg")
        self.assertEqual(a["goal"], "mantieni 25 kg, nessuna progressione")
        self.assertIsNone(a["update"])

    def test_repetition_week_no_step(self):
        ws = [wk("2026-10-27T10:00:00Z", UPPER_B), wk("2026-10-30T10:00:00Z", UPPER_B)]
        a = self.adv("Upper B", "Trazione verticale (macchina)", ws, today=D(2026, 11, 4))
        self.assertEqual(a["goal"], "mantieni 25 kg, obiettivo +1-2 ripetizioni (settimana di ripetizioni)")

    def test_new_scheda_start_load_wins_once(self):
        vdt = morning.dt.datetime.fromisoformat(SCHEDA["version_datetime"])
        curl8 = [("Curl Bicipiti (Manubrio)", [s(8, 12)] * 3)]
        old = [wk("2026-10-13T10:00:00Z", curl8), wk("2026-10-23T10:00:00Z", curl8)]
        a = self.adv("Upper B", "Curl Bicipiti (Manubrio)", old, today=D(2026, 10, 26), version_dt=vdt)
        self.assertEqual(a["load"], "9 kg per mano")  # partenza v17, niente doppio step
        self.assertIsNone(a["update"])

    def test_plates_unit(self):
        self.assertEqual(morning.fmt_loads([22.5] * 3, "kg di dischi"), "22,5 kg di dischi (11,25 per lato)")

    def test_bodyweight_at_top(self):
        ws = [wk("2026-09-01T10:00:00Z", [("Back Extension (Iperestensione)", [s(None, 15)] * 3)])]
        a = self.adv("Lower A", "Back Extension (Iperestensione)", ws)
        self.assertEqual(a["goal"], "mantieni corpo libero, al tetto")

    def test_hevy_total_of_two_dumbbells(self):
        ws = [wk("2026-10-10T10:00:00Z", [("Stacco da Terra Rumeno (Manubrio)", [s(8, 12)] * 3)]),   # per mano
              wk("2026-10-19T10:00:00Z", [("Stacco da Terra Rumeno (Manubrio)", [s(16, 12)] * 3)])]  # totale
        a = self.adv("Lower B", "Stacco da Terra Rumeno (Manubrio)", ws, today=D(2026, 10, 26))
        self.assertEqual(a["load"], "9 kg per mano (in Hevy 18 kg totali)")
        self.assertEqual(a["goal"], "STEP CONFERMATO: porta la routine a 18 kg")
        self.assertIn("a 8 kg", a["last"])

    def test_upright_row_load_is_fixed(self):
        a15 = [("Rematore Verticale (Bilanciere)", [s(15, 12)] * 4)]
        ws = [wk("2026-10-16T10:00:00Z", a15), wk("2026-10-24T10:00:00Z", a15)]
        a = self.adv("Upper C", "Rematore Verticale (Bilanciere)", ws, today=D(2026, 10, 27))
        self.assertEqual(a["goal"], "mantieni 15 kg (progressione sospesa)")
        self.assertIsNone(a["update"])

    def test_implicit_warmup_flagged(self):
        ws = [wk("2026-09-01T10:00:00Z", [("Alzate Laterali (Macchina)", [s(7.5, 12), s(10, 12), s(10, 12), s(10, 12)])])]
        a = self.adv("Upper A", "Alzate Laterali (Macchina)", ws)
        self.assertEqual(a["implicit"], D(2026, 9, 1))
        ws = [wk("2026-09-01T10:00:00Z", [("Alzate Laterali (Macchina)", [s(7.5, 12, "warmup")] + [s(10, 12)] * 3)])]
        self.assertIsNone(self.adv("Upper A", "Alzate Laterali (Macchina)", ws)["implicit"])

    def test_round_step(self):
        self.assertEqual(morning.deload(17.5, 2.5, 0.9), 15)
        self.assertEqual(morning.deload(7, 1, 0.9), 6)
        self.assertEqual(morning.deload(55, 5, 0.9), 50)


class TestMessage(unittest.TestCase):
    def msg(self, workouts, today):
        return morning.build_message(morning.classify(workouts, SCHEDA), SCHEDA, today)

    def test_rest_message(self):
        m = self.msg([wk("2026-10-10T12:00:00Z", LOWER_B)], D(2026, 10, 11))
        self.assertIn("Oggi riposo consigliato", m)
        self.assertIn("Domani: Upper A", m)
        self.assertIn("Mobilità e cervicale: almeno 3 volte a settimana", m)
        self.assertNotIn("Routine da aggiornare", m)

    def test_training_message_and_core(self):
        m = self.msg([wk("2026-10-10T12:00:00Z", LOWER_B)], D(2026, 10, 12))
        self.assertIn("Oggi: Upper A (circa 75-80 min)", m)
        self.assertIn("Rinforzo spalla a inizio seduta", m)
        self.assertTrue(m.endswith("Routine da aggiornare in Hevy: nessuna"))
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
        m = self.msg([wk("2026-11-09T12:00:00Z", UPPER_A)], D(2026, 11, 10))
        self.assertIn("Settimana S6 (09/11-15/11): Scarico.", m)
        self.assertIn("Settimana di scarico: carichi -10% (arrotondati al gradino) oppure una serie in meno, RPE 6.", m)
        self.assertNotIn("Routine da aggiornare", m)  # durante lo scarico le routine non si toccano
        self.assertNotIn("Settimana di scarico", self.msg([wk("2026-10-24T12:00:00Z", UPPER_A)], D(2026, 10, 28)))

    def test_upper_c_new_exercises(self):
        m = self.msg([wk("2026-09-01T10:00:00Z", UPPER_B)], D(2026, 9, 2))
        self.assertIn("Oggi: Upper C", m)
        self.assertIn("Lento in Avanti Seduto (Macchina) — 3x10-12 — partenza 12,5 kg", m)
        self.assertIn("Rinforzo spalla a inizio seduta", m)
        self.assertNotIn("Tirata verticale", m)

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
        uc = [e["name"] for e in SCHEDA["sessions"]["Upper C"]["exercises"]]
        self.assertIn("Rematore Verticale (Bilanciere)", uc)
        self.assertIn("Lento in Avanti Seduto (Macchina)", uc)
        self.assertNotIn("Tirata verticale a presa neutra", uc)

    def test_v17_values(self):
        def get(sess, name):
            return ex_cfg(sess, name)
        self.assertEqual(SCHEDA["version"], "v17")
        self.assertEqual(get("Upper A", "Chest Press (Macchina)")["start_load"], 20)
        self.assertEqual(get("Lower A", "Leg Press Orizzontale (Macchina)")["start_load"], 55)
        self.assertEqual(get("Lower B", "Hip Thrust (macchina)")["start_load"], 40)
        self.assertEqual(get("Upper B", "Curl Bicipiti (Manubrio)")["start_load"], 9)
        self.assertEqual(get("Lower B", "Abduzione Anche (Macchina)")["start_load"], 22.5)
        self.assertEqual(get("Upper A", "Panca Inclinata (Multipower)")["start_load"], 22.5)
        tr = get("Upper B", "Trazione verticale (macchina)")
        self.assertEqual((tr["sets"], tr["start_load"]), (3, 32.5))
        self.assertEqual(get("Upper C", "Lento in Avanti Seduto (Macchina)")["start_load"], 12.5)
        self.assertEqual(get("Lower B", "Hip Thrust (macchina)")["step_kg"], 5)
        self.assertEqual(get("Lower A", "Leg Press Orizzontale (Macchina)")["step_kg"], 5)

    def test_step_confirmed_in_footer(self):
        top = [("Trazione verticale (macchina)", [s(32.5, 12)] * 3)]
        ws = [wk("2026-10-27T10:00:00Z", UPPER_C), wk("2026-10-28T10:00:00Z", LOWER_B + [("Abduzione Anche (Macchina)", [s(22.5, 15)] * 3)]),
              wk("2026-10-29T10:00:00Z", UPPER_A), wk("2026-10-30T10:00:00Z", LOWER_A),
              wk("2026-10-31T10:00:00Z", top), wk("2026-11-01T10:00:00Z", UPPER_C),
              wk("2026-11-01T18:00:00Z", top)]
        cl = morning.classify(ws, SCHEDA)
        lines, _, _, _ = morning.session_block("Upper B", cl, SCHEDA, D(2026, 11, 1), "normal")
        self.assertIn("— 35 kg (STEP CONFERMATO: porta la routine a 35 kg", lines[0])

    def test_footer_lists_routines_to_update(self):
        top = [("Trazione verticale (macchina)", [s(32.5, 12)] * 3)]
        ws = [wk("2026-10-29T10:00:00Z", top), wk("2026-10-30T10:00:00Z", UPPER_A),
              wk("2026-10-31T10:00:00Z", top)]
        # dopo l'ultimo Upper B la rotazione proporrebbe Upper C: forziamo Upper B con il blocco
        cl = morning.classify(ws, SCHEDA)
        extra = {}
        morning.session_block("Upper B", cl, SCHEDA, D(2026, 11, 1), "normal", None, extra)
        self.assertIn(("Trazione verticale (macchina)", "35 kg"), extra["updates"])

    def test_two_steps_in_the_same_session(self):
        # più esercizi con step confermato nella stessa seduta: tutti nell'elenco finale
        a = [("Trazione verticale (macchina)", [s(32.5, 12)] * 3), ("Curl Bicipiti (Manubrio)", [s(9, 12)] * 3),
             ("Bicipiti Martello (Cavo)", [s(12.5, 12)] * 3)]
        ws = [wk("2026-10-27T10:00:00Z", UPPER_A), wk("2026-10-28T10:00:00Z", a),
              wk("2026-10-30T10:00:00Z", a), wk("2026-10-31T10:00:00Z", LOWER_B)]
        cl = morning.classify(ws, SCHEDA)
        extra = {}
        lines, _, _, _ = morning.session_block("Upper B", cl, SCHEDA, D(2026, 11, 1), "normal", None, extra)
        self.assertEqual([n for n, _v in extra["updates"]],
                         ["Trazione verticale (macchina)", "Curl Bicipiti (Manubrio)", "Bicipiti Martello (Cavo)"])
        self.assertEqual(dict(extra["updates"])["Curl Bicipiti (Manubrio)"], "10 kg")

    def test_tbar_note_from_november_2(self):
        ws = [wk("2026-10-30T10:00:00Z", LOWER_A)]
        self.assertNotIn("T-Bar Row", self.msg(ws, D(2026, 11, 1)))
        ws = [wk("2026-11-01T10:00:00Z", LOWER_A)]
        self.assertIn("T-Bar Row 3 x 10-12 a 10 kg", self.msg(ws, D(2026, 11, 2)))

    def test_implicit_warmup_note_in_message(self):
        ws = [wk("2026-10-20T10:00:00Z", [("Chest Press (Macchina)", [s(10, 12), s(20, 12), s(20, 12), s(20, 12)])]),
              wk("2026-10-27T10:00:00Z", LOWER_B)]
        m = self.msg(ws, D(2026, 10, 31))
        self.assertIn("Oggi: Upper A", m)
        self.assertIn("Prima serie contata come riscaldamento (non segnata warmup in Hevy): Chest Press (Macchina) 20/10.", m)

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
        rt = [routine("Giorno 1 - Upper A - Petto", [("Chest Press (Machine)", "T1", [self.W] + [s(22.5, 12)] * 3)])]
        lines, _, _, diffs = self.block(ws, rt)
        self.assertIn("Chest Press (Macchina) — 3x10-12 — 22,5 kg", lines[0])
        self.assertIn("Chest Press (Macchina) 3x 22,5 kg (scheda 3x 20 kg)", diffs)
        self.assertIn("Pushdown Tricipiti con Corda manca nella routine", diffs)

    def test_progression_on_top_of_routine_load(self):
        a = [("Trazione verticale (macchina)", "T2", [s(30, 12)] * 4)]
        ws = [wkt("2026-10-20T10:00:00Z", a), wkt("2026-10-23T10:00:00Z", a)]
        rt = [routine("Giorno 3 - Upper B", [("Vertical Traction (Machine)", "T2", [self.W] + [s(30, 12)] * 3)])]
        lines, _, _, diffs = self.block(ws, rt, "Upper B", D(2026, 10, 24))
        self.assertIn("Trazione verticale (macchina) — 3x10-12 — 32,5 kg (STEP CONFERMATO: porta la routine a 32,5 kg", lines[0])
        self.assertFalse([d for d in diffs if d.startswith("Trazione")])  # stesso risultato della scheda

    def test_no_progression_if_done_below_routine_load(self):
        a = [("Trazione verticale (macchina)", "T2", [s(27.5, 12)] * 4)]
        ws = [wkt("2026-10-20T10:00:00Z", a), wkt("2026-10-23T10:00:00Z", a)]
        rt = [routine("Giorno 3 - Upper B", [("Vertical Traction (Machine)", "T2", [s(30, 12)] * 4)])]
        lines, _, _, _ = self.block(ws, rt, "Upper B", D(2026, 10, 24))
        self.assertIn("— 30 kg (mantieni 30 kg, obiettivo +1-2 ripetizioni", lines[0])

    def test_routine_sets_count_wins(self):
        ws = [wkt("2026-10-16T10:00:00Z", [("Face Pull", "T3", [s(10, 15)] * 3)])]
        rt = [routine("Giorno 4 - Upper C", [("Face Pull", "T3", [s(10, 15)] * 2)])]
        lines, _, _, diffs = self.block(ws, rt, "Upper C")
        self.assertTrue(any(l.startswith("- Face Pull — 2x12-15 — 10 kg") for l in lines))
        self.assertTrue(any(d.startswith("Face Pull 2x 10 kg (scheda 3x") for d in diffs))

    def test_extra_and_suspended_exercises_reported(self):
        ws = [wkt("2026-10-16T10:00:00Z", [("Lento in Avanti Seduto (Macchina)", "T4", [s(12.5, 12)] * 3),
                                           ("Tirata verticale a presa neutra", "T5", [s(27.5, 12)] * 3)])]
        rt = [routine("Giorno 4 - Upper C", [("Seated Shoulder Press (Machine)", "T4", [s(12.5, 12)] * 3),
                                              ("Neutral Grip Lat Pulldown", "T5", [s(27.5, 12)] * 3)])]
        _, _, _, diffs = self.block(ws, rt, "Upper C")
        self.assertFalse([d for d in diffs if d.startswith("Lento")])
        self.assertIn("Tirata verticale a presa neutra è nella routine ma non in scheda", diffs)

    def test_dumbbell_total_and_per_leg_in_routine(self):
        ws = [wkt("2026-10-19T10:00:00Z", [("Step Up Manubrio", "T6", [s(14, 12)] * 6)])]
        rt = [routine("Giorno 5 - Lower B", [("Dumbbell Step Up", "T6", [s(10, 10, "warmup")] * 2 + [s(14, 12)] * 6)])]
        lines, _, _, _ = self.block(ws, rt, "Lower B")
        line = [l for l in lines if l.startswith("- Step Up")][0]
        self.assertIn("— 3x10-12 per gamba — 7 kg per mano (in Hevy 14 kg totali)", line)

    def test_deload_applies_to_routine_load(self):
        ws = [wkt("2026-10-22T10:00:00Z", [("Chest Press (Macchina)", "T1", [s(20, 12)] * 3)])]
        rt = [routine("Giorno 1 - Upper A", [("Chest Press (Machine)", "T1", [s(20, 12)] * 3)])]
        lines, _, _, _ = self.block(ws, rt, today=D(2026, 11, 10), ph="deload")
        self.assertIn("— 17,5 kg (scarico: -10% oppure una serie in meno, RPE 6", lines[0])

    def test_template_id_from_scheda_and_taratura_not_a_difference(self):
        rt = [routine("Giorno 3 - Upper B", [("Single Arm Cable Row", "D0C4A899", [s(7.5, 12)] * 3)])]
        lines, _, _, diffs = self.block([], rt, "Upper B")
        self.assertTrue(any(l.startswith("- Rematore al Cavo a Braccio Singolo — 3x10-12 — 7,5 kg") for l in lines))
        self.assertFalse([d for d in diffs if "Braccio" in d or "Single Arm" in d])
        # carico "da definire" in scheda: il carico della routine non è una differenza
        ws = [wkt("2026-10-21T10:00:00Z", [("Calf Raise Seduto", "T10", [s(0, 15)] * 3)])]
        rt = [routine("Giorno 2 - Lower A", [("Seated Calf Raise", "T10", [s(5, 15)] * 3)])]
        _, _, _, diffs = self.block(ws, rt, "Lower A")
        self.assertFalse([d for d in diffs if d.startswith("Calf")])

    def test_routine_updated_no_double_increase(self):
        old = [("Trazione verticale (macchina)", "T2", [s(30, 12)] * 4)]
        ws = [wkt("2026-10-20T10:00:00Z", old), wkt("2026-10-23T10:00:00Z", old)]
        rt = [routine("Giorno 3 - Upper B", [("Vertical Traction (Machine)", "T2", [s(32.5, 12)] * 4)])]
        lines, _, _, _ = self.block(ws, rt, "Upper B", D(2026, 10, 24))
        self.assertIn("— 32,5 kg (mantieni 32,5 kg, obiettivo +1-2 ripetizioni", lines[0])

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
        self.assertIn("9 kg per mano (in Hevy 18 kg totali) (STEP CONFERMATO: porta la routine a 18 kg", lines[0])

    def test_stale_check_only_within_same_routine(self):
        # 12,5 kg fatti nell'Upper C non rendono "non aggiornata" la routine Upper A a 10 kg.
        ws = [wkt("2026-10-20T10:00:00Z", [("Chest Press (Macchina)", "T1", [s(17.5, 12)] * 3),
                                           ("Alzate Laterali (Macchina)", "T9", [s(10, 12)] * 3)]),
              wkt("2026-10-24T10:00:00Z", [("Face Pull", "T3", [s(10, 15)] * 3),
                                           ("Alzate Laterali (Macchina)", "T9", [s(12.5, 12)] * 3)])]
        rt = [routine("Giorno 1 - Upper A", [("Lateral Raise (Machine)", "T9", [s(10, 12)] * 3)])]
        lines, _, _, _ = self.block(ws, rt, "Upper A", D(2026, 10, 25))
        line = [l for l in lines if l.startswith("- Alzate")][0]
        self.assertIn("— 10 kg (mantieni 10 kg", line)
        self.assertNotIn("non aggiornata", line)

    def test_walk_in_routine_ignored(self):
        ws = [wkt("2026-10-04T10:00:00Z", [("Camminata", "W1", [{"type": "normal", "distance_meters": 900}])])]
        rt = [routine("Giorno 2 - Lower A", [("Walking", "W1", [{"type": "normal"}])])]
        _, _, _, diffs = self.block(ws, rt, "Lower A")
        self.assertFalse([d for d in diffs if "Camminata" in d or "Walking" in d])

    def test_without_routines_scheda_only(self):
        ws = [wkt("2026-10-11T10:00:00Z", [("Chest Press (Macchina)", "T1", [s(17.5, 12)] * 3)])]
        lines, _, _, diffs = self.block(ws, None)
        self.assertIn("— 20 kg (mantieni 20 kg", lines[0])  # partenza v17 (prima seduta dopo la scheda)
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
