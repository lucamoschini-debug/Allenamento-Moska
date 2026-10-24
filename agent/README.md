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

- Hevy: solo `GET /v1/workouts` e `GET /v1/routines` (funzione `hevy_get`, unica che parla con Hevy).
- Telegram: solo `sendMessage` verso `TELEGRAM_CHAT_ID`; non legge messaggi in arrivo.
- Host consentiti: `api.hevyapp.com`, `api.telegram.org` (controllo in `_check_host`).
- Il token non viene mai stampato; gli errori sono riportati senza traceback e senza URL.
- Nessun dato viene salvato.

## Regole

- **Riconoscimento seduta**: dagli esercizi registrati (titoli Hevy della scheda, legacy compresi), non dal titolo dell'allenamento. A parità di punteggio vince la seduta attesa in rotazione. "Camminata" è ignorata, e così gli allenamenti il cui titolo inizia con "Core". Orari UTC convertiti in Europe/Rome.
- **Rotazione**: Upper A → Lower A → Upper B → Upper C → Lower B → 1-2 giorni di riposo. Dopo Lower B di ieri/oggi: riposo consigliato e seduta di domani, a meno che il riposo sia già stato fatto prima del Lower B (almeno un giorno libero dalla seduta precedente; il Core non conta come seduta). Allenamento già registrato oggi: lo dice e indica la successiva.
- **Serie di lavoro**: escluse le warmup; se nessuna è marcata e la prima ha carico < 85% di tutte le altre, è riscaldamento. failure e dropset contano. Step-up: due righe consecutive (una per gamba) = una serie.
- **Storico**: per i carichi si usano solo gli `hevy_titles` dell'esercizio, non i legacy. Se l'esercizio compare in più sedute (es. Deltoid Machine, Calf Raise Seduto), vale l'ultima esecuzione.
- **Progressione**: tutte le serie di lavoro al tetto (`rep_max`) nelle ultime due esecuzioni, con la precedente ad almeno gli stessi carichi → +`step_kg`; altrimenti stesso carico e +1-2 ripetizioni. Carichi per serie mantenuti (es. Leg Press 50/50/50/60).
- **Nuova scheda**: per ogni esercizio, la prima seduta dopo `version_datetime` usa il carico di partenza della scheda (se numerico); dalla seduta successiva vale la regola di progressione.
- **Routine Hevy**: la base è `scheda.json`, ma ogni mattina lo script legge le routine Hevy (una per seduta, riconosciuta dal nome, es. "Upper A") e le confronta con la scheda. Esercizi abbinati dal codice esercizio Hevy (le routine hanno i nomi inglesi); per un esercizio mai registrato il codice si mette in `hevy_template_ids`. Se serie o carichi non coincidono vale la routine: la progressione si calcola sul carico della routine (+1 gradino se le ultime due sedute sono al tetto con almeno quel carico); scarico, settimana di ripetizioni e progressione sospesa restano validi. Le differenze, gli esercizi mancanti nella routine e quelli in più finiscono nelle note. Quando un aumento è maturo il messaggio dice "aumento maturo (2 sedute al tetto): porta la routine Hevy a X" (X nelle unità di Hevy, es. totale dei manubri): gli aumenti si registrano nella routine. Se l'ultima seduta della stessa routine è già un gradino sopra la routine, il messaggio dice "routine Hevy non aggiornata: portala a X" e non sale una seconda volta. Se le routine non si leggono, vale solo la scheda.
- **Senza storico**: `start_load` numerico → "partenza X, poi tara a RPE 7-8"; "da definire" → testo di taratura.
- **Unità**: `kg totali`, `kg per mano`, `kg di dischi` (Multipower: solo dischi, mostrato anche per lato), `kg per mano, totale in Hevy` (Stacco rumeno e Step-up: dal `hevy_total_from` in Hevy si segna il totale dei due manubri; lo storico viene diviso per due).
- **Ciclo**: settimane in `scheda.json` → `cycle.weeks` (S3-S8, 19/10-29/11). Settimane di scarico (S4, S8): -10% arrotondato al gradino, RPE 6, nessuna progressione. S6: stessi carichi, solo ripetizioni. Dopo uno scarico si riparte dai carichi di prima (le sedute di scarico non contano per lo storico). Dopo il 29/11 "ciclo concluso", carichi mantenuti.
- **Esercizi sospesi**: `suspended` (es. Lento in Avanti) compare come "SOSPESO" con il carico di ripartenza; `progression_suspended` (Chest Press) mantiene il carico.
- **Sedute saltate**: se dopo la seduta che precede X nella rotazione ne è stata fatta un'altra e X non è stata fatta dopo, il messaggio lo segnala nelle note.
- **Giorni di riposo**: messaggio breve con la seduta di domani e "Core o Mobilità e cervicale, se hai tempo". Il core non è legato alle sedute.
- **Camminate**: il Cammino è in pausa, il messaggio non contiene camminate. Le voci "Camminata" registrate in Hevy sono ignorate.

## Export per il coach

```
python agent/export_coach.py <cartella fuori dal repository>
```

Crea un file Excel con le routine Hevy attuali, lo storico di tutte le serie, un riepilogo per esercizio e data (formule) e le differenze tra scheda e routine. Legge Hevy solo in GET tramite `morning.py`. Il file contiene dati reali: lo script rifiuta una cartella di uscita dentro il repository e `.gitignore` esclude `*.xlsx`. Richiede `openpyxl`.

## File

- `scheda.json` – scheda v15 (20261019-2330): nomi degli esercizi come in Hevy (italiano), rotazione, ciclo, note.
- `export_coach.py` – export Excel per il coach (vedi sopra).
- `tests/` – test e fixture con dati **inventati** (`make_fixture.py` la rigenera).
