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
- **Progressione (STEP CONFERMATO)**: se nelle ultime due sedute valide consecutive tutte le serie di lavoro sono al tetto del range, con almeno il carico di base, scatta +1 gradino (`step_kg`: macchine e cavi 2,5, Leg Press e Hip Thrust 5, manubri 1). La riga dell'esercizio dice "STEP CONFERMATO: porta la routine a X kg" finché non c'è una seduta registrata al nuovo carico (o la routine è aggiornata); in fondo al messaggio "Routine da aggiornare in Hevy:" con l'elenco oppure "nessuna". Altrimenti "mantieni X kg, obiettivo +1-2 ripetizioni". Mai aumenti senza step confermato. Se la prima serie è stata contata come riscaldamento con la regola dell'85% (nessuna serie warmup in Hevy), una nota lo dice.
- **Carichi**: dalla v18 la scheda non ha carichi di partenza: definisce esercizi, serie, range e regole; i carichi li decide Hevy (routine come base, allenamenti registrati per lo step). Tra scheda e routine si segnalano esercizi e serie diversi (e un carico in routine dove la scheda dice corpo libero).
- **Routine Hevy**: la base è `scheda.json`, ma ogni mattina lo script legge le routine Hevy (una per seduta, riconosciuta dal nome, es. "Upper A") e le confronta con la scheda. Esercizi abbinati dal codice esercizio Hevy (le routine hanno i nomi inglesi); per un esercizio mai registrato il codice si mette in `hevy_template_ids`. Se serie o carichi non coincidono vale la routine: la progressione si calcola sul carico della routine (+1 gradino se le ultime due sedute sono al tetto con almeno quel carico); scarico, settimana di ripetizioni e progressione sospesa restano validi. Le differenze, gli esercizi mancanti nella routine e quelli in più finiscono nelle note. Quando un aumento è maturo il messaggio dice "aumento maturo (2 sedute al tetto): porta la routine Hevy a X" (X nelle unità di Hevy, es. totale dei manubri): gli aumenti si registrano nella routine. Se l'ultima seduta della stessa routine è già un gradino sopra la routine, il messaggio dice "routine Hevy non aggiornata: portala a X" e non sale una seconda volta. Se le routine non si leggono, vale solo la scheda.
- **Senza storico**: carico della routine Hevy se c'è, altrimenti testo di taratura (RPE 7-8).
- **Unità**: `kg totali`, `kg per mano`, `kg di dischi` (Multipower: solo dischi, mostrato anche per lato), `kg per mano, totale in Hevy` (Stacco rumeno e Step-up: dal `hevy_total_from` in Hevy si segna il totale dei due manubri; lo storico viene diviso per due).
- **Ciclo**: settimane in `scheda.json` → `cycle.weeks` (dato modificabile, non nel codice). v18: S4 26/10-01/11 normale; S5 02-08/11 normale; S6 09-15/11 scarico; S7 16-22/11 ripresa; S8 23-29/11 normale. Lo step può scattare in S4, S5, S7 e S8, mai in S6. Scarico: una serie in meno per esercizio e carichi -10% arrotondati al gradino (almeno un gradino), RPE 6, nessuna riga "routine da aggiornare". Le sedute registrate nella settimana di scarico sono escluse dalla progressione: non contano né a favore né contro, "ultima volta" le salta e due sedute valide separate da una seduta di scarico non sono consecutive. Dopo il 29/11: "ciclo concluso: serve la nuova scheda", carichi mantenuti senza progressione.
- **Esercizi sospesi**: `suspended` compare come "SOSPESO" con il carico di ripartenza; `progression_suspended` (Rematore Verticale, fermo fino al controllo dal fisioterapista) mantiene il carico.
- **Sedute saltate**: se dopo la seduta che precede X nella rotazione ne è stata fatta un'altra e X non è stata fatta dopo, il messaggio lo segnala nelle note.
- **Promemoria (da `scheda.json`)**: Rinforzo spalla a inizio Upper A e Upper C (una riga, nessun calcolo: proposta da confermare al fisioterapista); nota T-Bar Row in Upper B dal 02/11; nei giorni di riposo "Mobilità e cervicale: almeno 3 volte a settimana". Core: nessuna prescrizione. Nessuna camminata.
- **Camminate**: il Cammino è in pausa, il messaggio non contiene camminate. Le voci "Camminata" registrate in Hevy sono ignorate.

## Export per il coach

```
python agent/export_coach.py <cartella fuori dal repository>
```

Crea `AAAAMMGG - Routine e storico carichi Hevy Luca.xlsx`, un file con due fogli: **Allenamenti** (una riga per serie, riscaldamenti inclusi e marcati, data, seduta riconosciuta, esercizio, tipo di serie, kg, kg per mano per Stacco rumeno e Step Up, ripetizioni, "scarico" nelle settimane di scarico) e **Routine** (per ogni esercizio delle routine Hevy: serie di riscaldamento e di lavoro, range di ripetizioni, carichi). Legge Hevy solo in GET con paginazione, tramite `morning.py`. Si lancia a mano, la domenica sera. Il file contiene dati reali: lo script rifiuta una cartella di uscita dentro il repository e `.gitignore` esclude `*.xlsx`. Richiede `openpyxl`.

## File

- `scheda.json` – scheda v18 (20261025-2348): nomi degli esercizi come in Hevy (italiano), rotazione, ciclo, note.
- `export_coach.py` – export Excel per il coach (vedi sopra).
- `tests/` – test e fixture con dati **inventati** (`make_fixture.py` la rigenera).
