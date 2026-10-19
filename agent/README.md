# Agente del mattino

`morning.py` legge gli allenamenti da Hevy, calcola la prossima seduta della scheda (`scheda.json`) con i carichi suggeriti e invia un messaggio Telegram. Tutto a regole, riproducibile, solo libreria standard di Python (3.9+).

## Uso

```
python agent/morning.py                # calcola e invia
python agent/morning.py --dry-run      # stampa il messaggio senza inviarlo
python agent/morning.py --dry-run --fixture agent/tests/fixture_workouts.json --today 2026-09-09
python -m unittest discover -s agent/tests
```

Variabili d'ambiente: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. La chiave Hevy è aggiunta dal proxy dell'ambiente: lo script non la legge né la imposta.

## Sicurezza

- Hevy: solo `GET /v1/workouts` (funzione `hevy_get`, unica che parla con Hevy).
- Telegram: solo `sendMessage` verso `TELEGRAM_CHAT_ID`; non legge messaggi in arrivo.
- Host consentiti: `api.hevyapp.com`, `api.telegram.org` (controllo in `_check_host`).
- Il token non viene mai stampato; gli errori sono riportati senza traceback e senza URL.
- Nessun dato viene salvato.

## Regole

- **Riconoscimento seduta**: dagli esercizi registrati (titoli Hevy della scheda, legacy compresi), non dal titolo dell'allenamento. A parità di punteggio vince la seduta attesa in rotazione. "Camminata" è ignorata, e così gli allenamenti il cui titolo inizia con "Core". Orari UTC convertiti in Europe/Rome.
- **Rotazione**: Upper A → Lower A → Upper B → Upper C → Lower B → 1-2 giorni di riposo. Dopo Lower B di ieri/oggi: riposo consigliato e seduta di domani. Allenamento già registrato oggi: lo dice e indica la successiva.
- **Serie di lavoro**: escluse le warmup; se nessuna è marcata e la prima ha carico < 85% di tutte le altre, è riscaldamento. failure e dropset contano. Step-up: due righe consecutive (una per gamba) = una serie.
- **Storico**: per i carichi si usano solo gli `hevy_titles` dell'esercizio, non i legacy. Se l'esercizio compare in più sedute (es. Deltoid Machine, Calf Raise Seduto), vale l'ultima esecuzione.
- **Progressione**: tutte le serie di lavoro al tetto (`rep_max`) nelle ultime due esecuzioni, con la precedente ad almeno gli stessi carichi → +`step_kg`; altrimenti stesso carico e +1-2 ripetizioni. Carichi per serie mantenuti (es. Leg Press 50/50/50/60).
- **Nuova scheda**: per ogni esercizio, la prima seduta dopo `version_datetime` usa il carico di partenza della scheda (se numerico); dalla seduta successiva vale la regola di progressione.
- **Senza storico**: `start_load` numerico → "partenza X, poi tara a RPE 7-8"; "da definire" → testo di taratura.
- **Unità**: `kg totali`, `kg per mano`, `kg di dischi` (Multipower: solo dischi, mostrato anche per lato).
- **Ciclo**: 26/10-01/11 scarico (-10% arrotondato al gradino, RPE 6, nessuna progressione); dopo il 01/11 "ciclo concluso", carichi mantenuti.
- **Giorni di riposo**: messaggio breve con la seduta di domani e "Core o Mobilità e cervicale, se hai tempo". Il core non è legato alle sedute.
- **Camminate**: il Cammino è in pausa, il messaggio non contiene camminate. Le voci "Camminata" registrate in Hevy sono ignorate.

## File

- `scheda.json` – scheda v14 (20261017-2310; carichi di partenza dalla v12): nomi degli esercizi come in Hevy (italiano), rotazione, ciclo, note.
- `tests/` – test e fixture con dati **inventati** (`make_fixture.py` la rigenera).
