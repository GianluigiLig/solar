# Pacchetto `solar`

`solar` è un pacchetto applicativo dedicato al trattamento di dati provenienti da impianti fotovoltaici.

Il pacchetto contiene la logica necessaria per:

- leggere e validare la configurazione di un impianto;
- acquisire dati da sorgenti supportate;
- normalizzare i dati grezzi in un formato coerente;
- costruire un dataset arricchito per analisi energetiche;
- calcolare KPI fotovoltaici come Performance Ratio, availability e percentuale di intervalli validi;
- offrire un runner unico utilizzabile da script batch, notebook o orchestratori esterni.

Il README descrive il pacchetto come componente autonomo. Un nuovo sviluppatore può partire da qui per capire dove si trovano le responsabilità principali e quali moduli modificare in caso di nuove sorgenti, nuove trasformazioni o nuovi KPI.

---

## Obiettivo del pacchetto

Il problema gestito da `solar` è trasformare dati eterogenei di impianto in dataset coerenti e utilizzabili per analisi operative.

In generale, il flusso è:

```text
configurazione impianto
        ↓
acquisizione dati dalla sorgente
        ↓
normalizzazione raw → silver
        ↓
costruzione dataset gold
        ↓
calcolo KPI
```

Il pacchetto separa tre aspetti:

1. **Configurazione**
   - informazioni sull'impianto;
   - parametri di calcolo;
   - dispositivi;
   - connessioni verso le sorgenti dati;
   - regole minime di qualità del dato.

2. **Sorgenti**
   - client per interrogare API esterne;
   - adapter per convertire la risposta della sorgente in un formato interno coerente;
   - regole di naming e salvataggio dei dataset prodotti.

3. **Dominio fotovoltaico**
   - colonne canoniche;
   - trasformazioni temporali;
   - trasformazioni device-based;
   - costruzione gold;
   - calcolo KPI.

---

## Struttura del pacchetto

```text
solar/
├── README.md
├── __init__.py
├── config/
│   ├── __init__.py
│   └── model.py
├── domain/
│   ├── __init__.py
│   ├── schemas.py
│   ├── time.py
│   ├── transforms.py
│   ├── gold.py
│   ├── kpi.py
│   └── string_currents.py
├── sources/
│   ├── __init__.py
│   ├── inaccess.py
│   └── meteocontrol.py
└── orchestration/
    ├── __init__.py
    └── runner.py
```

---

## Flusso dati

Il pacchetto segue una logica a livelli.

### Bronze

Il livello bronze contiene i dati il più possibile vicini alla risposta originale della sorgente.

In questa fase vengono gestiti:

- intervallo temporale richiesto;
- parametri di fetch;
- chiamata verso la sorgente;
- salvataggio del payload raw;
- tracciamento dei metadati necessari alle fasi successive.

La bronze non dovrebbe contenere logica di business complessa. Il suo scopo è conservare una copia grezza e riproducibile del dato acquisito.

### Silver

Il livello silver converte il dato raw in tabelle normalizzate.

In questa fase vengono gestiti:

- parsing delle date;
- conversione dei valori numerici;
- normalizzazione dei nomi dispositivo;
- costruzione della griglia `timestamp × device`;
- gestione dei valori mancanti;
- uniformazione delle colonne tra sorgenti diverse.

La silver è il primo livello in cui sorgenti diverse iniziano ad assumere una forma comune.

### Gold

Il livello gold costruisce il dataset arricchito usato per KPI e analisi.

In questa fase vengono gestiti:

- unione dei dataset silver;
- pivot delle misure per dispositivo;
- calcolo della potenza totale inverter;
- calcolo dell'energia prodotta;
- calcolo dell'energia netta;
- calcolo dell'energia teorica;
- calcolo dell'irraggiamento medio;
- preparazione delle colonne temporali locali;
- costruzione delle colonne necessarie ai KPI.

La gold rappresenta il dataset principale su cui fare analisi fotovoltaiche.

### KPI

Il livello KPI aggrega il dataset gold su una frequenza richiesta.

Sono supportate frequenze come:

- `day`;
- `month`;
- `year`;
- `yom`, cioè Year of Monitoring.

I KPI includono:

- Performance Ratio;
- irraggiamento medio;
- energia netta;
- energia inverter;
- energia teorica;
- availability per inverter;
- availability complessiva di impianto;
- percentuale di intervalli validi.

---

## Configurazione

La configurazione viene gestita tramite un file YAML unico.

Il modulo principale è:

```text
solar/config/model.py
```

La configurazione viene letta e trasformata in una vista tipizzata tramite `SolarConfig`.

### Sezioni principali dello YAML

Uno YAML di impianto deve contenere almeno sezioni di questo tipo:

```yaml
metadata:
  plant_name: "nome_impianto"
  plant_nominal_power_kw: 1000
  PAC_date: "2023-01-01"
  OM_date: "2023-01-01"
  timezone: "Europe/Rome"

pr_calculation_parameters:
  time_interval_hours: 0.25
  irradiance_threshold_w_m2: 50
  has_meter: false
  degradation_factor: 0.0

data_quality:
  pyranometers:
    outlier_detection: true
    outlier_threshold: 0.05

devices_registry:
  inverters:
    inverter_01:
      nominal_power_w: 100000

connections:
  ...
```

La sezione `connections` cambia in base alla sorgente dati utilizzata.

### Campi obbligatori

La validazione minima richiede:

- `metadata.plant_name`;
- `metadata.plant_nominal_power_kw`;
- `metadata.PAC_date`;
- `metadata.OM_date`;
- `pr_calculation_parameters.time_interval_hours`;
- `pr_calculation_parameters.irradiance_threshold_w_m2`;
- `connections`.

Se uno di questi campi manca, il caricamento della configurazione solleva un errore.

### `SolarConfig`

`SolarConfig` è una vista strutturata dello YAML.

Contiene proprietà già pronte per il resto del codice, tra cui:

- `source`;
- `plant_name`;
- `timezone`;
- `plant_nominal_power_kw`;
- `pac_date`;
- `om_date`;
- `time_interval_hours`;
- `irradiance_threshold_w_m2`;
- `has_meter`;
- `degradation_factor`;
- `devices_registry`;
- `data_quality`;
- `connections`;
- `inverter_nominal_power_w_map`.

In questo modo il codice non deve accedere continuamente allo YAML tramite chiavi annidate.

---

## Moduli di dominio

I moduli sotto `solar/domain/` contengono la logica fotovoltaica vera e propria.

---

### `domain/schemas.py`

Centralizza i nomi delle colonne usate dal pacchetto.

Contiene:

- `SilverRawColumns`;
- `CanonicalColumns`;
- `AvailabilityColumns`.

Serve a evitare stringhe duplicate e incoerenze tra moduli diversi.

Esempi di colonne silver:

- `datetime_utc`;
- `device_id`;
- `device_name`;
- `P_AC`;
- `SRAD`;
- `E_INT_MEASURED`.

Esempi di colonne gold/canoniche:

- `datetime_local`;
- `datetime_utc`;
- `inverter_active_power_sum`;
- `inverter_energy_sum`;
- `net_energy`;
- `theoretical_energy`;
- `irradiance_mean`;
- `check_irradiance_threshold`;
- `PR`;
- `valid_interval_pct`.

Quando si aggiungono nuove colonne stabili, è preferibile registrarle qui invece di scriverle direttamente nei moduli operativi.

---

### `domain/time.py`

Contiene funzioni temporali specifiche del dominio fotovoltaico.

Funzioni principali:

- `calculate_yom`;
- `calculate_year_since_pac`;
- `get_grouping_columns_and_intervals`.

#### `calculate_yom`

Calcola lo Year of Monitoring a partire dalla data di Operation & Maintenance.

Serve quando i KPI devono essere letti non solo per anno solare, ma anche rispetto all'anno operativo dell'impianto.

#### `calculate_year_since_pac`

Calcola l'anno operativo a partire dalla PAC date.

Viene usata nel calcolo del Performance Ratio, perché il degrado dei pannelli dipende dagli anni trascorsi dalla PAC.

#### `get_grouping_columns_and_intervals`

Restituisce:

- colonne di raggruppamento per la frequenza richiesta;
- numero atteso di intervalli nel periodo.

Esempi:

```text
freq="day"   → group by year, month, day
freq="month" → group by year, month
freq="year"  → group by year
freq="yom"   → group by yom
```

---

### `domain/transforms.py`

Contiene trasformazioni comuni sui dati device-based.

Funzioni principali:

- `make_device_time_grid`;
- `pivot_device_measurements`.

#### `make_device_time_grid`

Costruisce una griglia completa:

```text
timestamp × device
```

Questa funzione è importante perché consente di rappresentare anche i casi in cui un dispositivo non ha inviato dati per uno o più intervalli.

Senza questa griglia, un dispositivo assente in una finestra temporale potrebbe semplicemente scomparire dal dataset, rendendo più difficile distinguere tra:

- valore realmente assente;
- dispositivo non previsto;
- errore di acquisizione;
- buco temporale.

#### `pivot_device_measurements`

Trasforma misure in formato lungo:

```text
datetime_utc | device_name | valore
```

in formato largo:

```text
datetime_utc | metrica_device_1 | metrica_device_2 | ...
```

Questa struttura è usata dalla gold per sommare potenze, energie e misure per dispositivo.

---

### `domain/gold.py`

Contiene la costruzione del dataset gold.

Componenti principali:

- `normalize_datetime_index`;
- `build_solar_gold_dataset`;
- `SolarGoldProcessor`.

#### `normalize_datetime_index`

Normalizza la colonna temporale:

- converte a datetime;
- elimina timestamp non validi;
- rimuove duplicati;
- ordina cronologicamente.

#### `build_solar_gold_dataset`

È la funzione che arricchisce il dataset silver unificato.

Calcola e prepara:

- `datetime_utc`;
- `datetime_local`;
- `yom`;
- `year`;
- `month`;
- `day`;
- irraggiamento medio;
- check soglia irraggiamento;
- irradiation;
- potenza inverter per dispositivo;
- potenza totale inverter;
- energia inverter per dispositivo;
- energia totale inverter;
- energia netta;
- energia teorica.

La funzione usa i parametri della configurazione, in particolare:

- potenza nominale dell'impianto;
- soglia minima di irraggiamento;
- intervallo temporale in ore;
- presenza o assenza del meter;
- data di Operation & Maintenance.

#### `SolarGoldProcessor`

È il componente che prepara gli input silver, li unisce e genera il dataset gold.

La logica principale è:

```text
silver inverters
silver pyranometers
eventuale silver meters
        ↓
pivot per dispositivo
        ↓
concat su datetime
        ↓
report qualità pre-gold
        ↓
fill dei valori mancanti
        ↓
costruzione gold
```

Il report pre-gold aiuta a verificare la quantità di valori mancanti prima che vengano sostituiti. Questo è utile per debug e controlli di qualità.

---

### `domain/kpi.py`

Contiene il calcolo dei KPI fotovoltaici.

Componenti principali:

- `calculate_pr`;
- `calculate_valid_interval_pct`;
- `calculate_irr_mean`;
- `compute_availability_kpis`;
- `SolarKpiProcessor`;
- `build_cs_intervals`.

#### `calculate_pr`

Calcola il Performance Ratio:

```text
PR = energia netta / energia teorica corretta per degrado
```

Il degrado viene applicato in base agli anni trascorsi dalla PAC.

#### `calculate_valid_interval_pct`

Calcola la percentuale di intervalli validi rispetto agli intervalli attesi.

È utile per capire quanto il KPI sia basato su dati completi.

#### `calculate_irr_mean`

Calcola l'irraggiamento medio a partire da una o più colonne di piranometri.

Può applicare una logica semplice di outlier detection tra sensori, se abilitata da configurazione.

#### `compute_availability_kpis`

Calcola availability e downtime a partire dalle potenze inverter.

La logica distingue:

- ore teoricamente rilevanti, cioè con irraggiamento sopra soglia;
- intervalli di Contractual Stop;
- intervalli in cui un inverter risulta fermo;
- availability per singolo inverter;
- availability pesata di impianto.

#### `SolarKpiProcessor`

È il processore principale dei KPI.

Riceve:

- dataframe gold;
- configurazione;
- frequenza richiesta;
- data di riferimento;
- eventuali intervalli di Contractual Stop.

Restituisce un dataframe KPI aggregato.

#### `build_cs_intervals`

Costruisce intervalli di Contractual Stop a partire da un dataframe ticket.

Il dataframe ticket deve contenere colonne come:

- `impianto`;
- `data_inizio_disservizio`;
- `data_fine_disservizio`;
- `causa_problematica_finale`.

Questa funzione è separata dal calcolo KPI principale, ma resta nello stesso modulo perché i Contractual Stop vengono usati direttamente nel calcolo availability.

---

### `domain/string_currents.py`

Contiene una feature separata dedicata alle correnti di stringa.

Componenti principali:

- `StringCurrentResult`;
- `normalize_string_current_dataframe`.

Questo modulo è volutamente separato dalla pipeline principale perché le string currents possono avere:

- input diversi;
- struttura diversa;
- output diversi;
- eventuali destinazioni diverse.

Al momento contiene una normalizzazione minima delle colonne di corrente, convertendo in numerico le colonne che iniziano con prefissi come `i_` o `current`.

---

## Sorgenti dati

I moduli sotto `solar/sources/` gestiscono le integrazioni con le sorgenti supportate.

Ogni sorgente è composta da due elementi principali:

1. **Client**
   - conosce API, autenticazione, endpoint e parametri HTTP;
   - recupera il payload raw.

2. **Adapter**
   - genera le richieste bronze;
   - costruisce le chiavi di salvataggio;
   - converte payload bronze in dataframe silver;
   - applica mapping e normalizzazioni specifiche della sorgente.

---

### `sources/__init__.py`

Espone le factory:

- `get_source_adapter`;
- `get_source_client`.

Esempio:

```python
from solar.sources import get_source_adapter, get_source_client

adapter = get_source_adapter("inaccess", lake_root="solar-dev")
client = get_source_client(
    "inaccess",
    connection_config={"api_key": "..."},
)
```

Se viene richiesta una sorgente non supportata, viene sollevato un errore di configurazione.

Sorgenti attualmente supportate:

- `inaccess`;
- `meteocontrol`.

---

### `sources/inaccess.py`

Contiene l'integrazione con Inaccess.

Componenti principali:

- `InaccessClient`;
- `InaccessPathsConfig`;
- `InaccessAdapter`.

#### `InaccessClient`

Gestisce:

- API key;
- base URL;
- timeout;
- verifica SSL;
- chiamata endpoint;
- validazione minima della risposta.

Il metodo principale è `fetch_data`.

Richiede parametri come:

```python
{
    "source_id": "..."
}
```

#### `InaccessAdapter`

Gestisce:

- lettura della sezione `connections`;
- generazione delle richieste bronze per strumenti e dispositivi;
- costruzione dei path bronze e silver;
- trasformazione dei payload raw in dataframe silver;
- applicazione dei controlli di qualità configurati;
- costruzione della griglia temporale per dispositivo;
- recupero della mappa di potenza nominale inverter.

La trasformazione silver produce dati normalizzati con colonne coerenti, tra cui:

- `datetime_utc`;
- `device_name`;
- colonna valore configurata.

---

### `sources/meteocontrol.py`

Contiene l'integrazione con MeteoControl.

Componenti principali:

- `MeteoControlClient`;
- `MeteoControlPathsConfig`;
- `MeteoControlAdapter`.

#### `MeteoControlClient`

Gestisce:

- API key;
- username;
- password;
- autenticazione HTTP Basic;
- base URL;
- timeout;
- chiamate per chunk temporali.

Il metodo principale è `fetch_data`.

Richiede parametri come:

```python
{
    "connection_id": "...",
    "instrument": "inverters"
}
```

oppure:

```python
{
    "connection_id": "...",
    "instrument": "sensors"
}
```

#### `MeteoControlAdapter`

Gestisce:

- mapping degli inverter;
- mapping dei piranometri;
- flatten della risposta API;
- conversione dei valori numerici;
- conversione dei timestamp;
- normalizzazione dei nomi dispositivo;
- costruzione della griglia temporale completa;
- produzione dei dataframe silver.

Per gli inverter vengono gestite colonne come:

- `P_AC`;
- `E_TOTAL`;
- `I_AC`;
- `U_AC`;
- altri valori elettrici disponibili dalla sorgente.

Per i piranometri viene gestita la colonna di irraggiamento canonica.

---

## Orchestrazione

Il modulo principale è:

```text
solar/orchestration/runner.py
```

Contiene:

- `build_lake`;
- `run_solar_pipeline`;
- `calculate_solar_kpis_from_gold`.

---

### `run_solar_pipeline`

È il punto di ingresso principale per eseguire una o più fasi della pipeline.

Esempio:

```python
from solar.orchestration.runner import run_solar_pipeline

results = run_solar_pipeline(
    config_path="config/solar_asset.yaml",
    source="inaccess",
    stages=["bronze", "silver", "gold"],
    bucket="my-bucket",
    lake_root="solar-dev",
    connection_config={
        "api_key": "...",
    },
    execution_date="2026-06-01",
)
```

Parametri principali:

- `config_path`: path dello YAML;
- `source`: sorgente dati, ad esempio `inaccess` o `meteocontrol`;
- `stages`: lista delle fasi da eseguire;
- `bucket`: bucket o contenitore logico di destinazione;
- `lake_root`: prefisso root sotto cui salvare i dataset;
- `connection_config`: credenziali e parametri della sorgente;
- `storage`: storage già inizializzato, opzionale;
- `local_storage_root`: root locale per esecuzioni in locale;
- `gcs_client`: client cloud già inizializzato, opzionale;
- `execution_date`: data logica di esecuzione.

Se viene eseguita la fase `bronze`, `connection_config` è obbligatorio perché serve interrogare la sorgente.

---

### `calculate_solar_kpis_from_gold`

Calcola i KPI partendo da un dataframe gold già disponibile.

Esempio:

```python
from solar.orchestration.runner import calculate_solar_kpis_from_gold

df_kpi = calculate_solar_kpis_from_gold(
    config_path="config/solar_asset.yaml",
    source="inaccess",
    gold_df=df_gold,
    freq="day",
    year=2026,
    month=6,
    day=1,
    cs_intervals=[],
)
```

Parametri principali:

- `config_path`: path dello YAML;
- `source`: sorgente dati;
- `gold_df`: dataframe gold di input;
- `freq`: frequenza di aggregazione;
- `year`, `month`, `day`: data di riferimento del calcolo;
- `cs_intervals`: eventuali intervalli di Contractual Stop.

---

## Path e prefissi di salvataggio

Il parametro `lake_root` consente di controllare il prefisso di salvataggio.

Esempio:

```python
run_solar_pipeline(
    ...,
    bucket="my-bucket",
    lake_root="solar-dev",
)
```

produce path sotto una radice logica simile a:

```text
my-bucket/solar-dev/...
```

Questo permette di separare ambienti diversi, ad esempio:

```text
solar-dev
solar-test
solar-prod
```

oppure elaborazioni diverse dello stesso impianto.

Il valore non dovrebbe essere hardcoded nei moduli di dominio. È preferibile riceverlo dal runner, da variabili ambiente o da parametri dell'orchestratore.

---

## Come aggiungere una nuova sorgente

Per aggiungere una nuova sorgente dati, in genere bisogna:

1. creare un nuovo modulo in `solar/sources/`;
2. implementare un client per la chiamata API;
3. implementare un adapter per bronze e silver;
4. definire eventuali path specifici della sorgente;
5. registrare la sorgente in `solar/sources/__init__.py`;
6. aggiornare lo YAML con la nuova struttura `connections`;
7. verificare che l'output silver rispetti le colonne attese dalla gold.

La cosa più importante è che la silver prodotta dalla nuova sorgente sia compatibile con la gold.

In particolare, per la gold servono almeno:

- timestamp UTC;
- nome dispositivo;
- potenza attiva inverter;
- irraggiamento;
- eventuale energia meter, se `has_meter=True`.

---

## Come aggiungere un nuovo KPI

Per aggiungere un nuovo KPI, il punto principale è:

```text
solar/domain/kpi.py
```

La procedura consigliata è:

1. verificare che la gold contenga già le colonne necessarie;
2. se mancano, aggiungerle in `domain/gold.py`;
3. implementare il calcolo nel processor KPI;
4. decidere se il KPI deve essere calcolato per tutte le frequenze o solo per alcune;
5. aggiornare l'output finale mantenendo coerenti le colonne temporali;
6. aggiungere test o controlli su dataframe di esempio.

Se il KPI è puramente aggregato, probabilmente va in `SolarKpiProcessor`.

Se invece richiede nuove colonne row-level, probabilmente la preparazione va nella gold.

---

## Debug e controlli pratici

Quando qualcosa non torna, conviene controllare il flusso in quest'ordine.

### 1. Configurazione

Verificare:

- `plant_name`;
- timezone;
- potenza nominale impianto;
- date PAC e OM;
- intervallo temporale;
- soglia di irraggiamento;
- presenza o assenza meter;
- struttura `connections`;
- nomi dispositivo;
- potenze nominali inverter.

### 2. Bronze

Verificare:

- se la sorgente restituisce dati;
- se l'intervallo temporale richiesto è corretto;
- se le credenziali sono valide;
- se il payload raw contiene le misure attese.

### 3. Silver

Verificare:

- presenza di `datetime_utc`;
- presenza di `device_name`;
- presenza delle colonne valore;
- numero di righe attese;
- griglia `timestamp × device`;
- eventuali valori mancanti.

### 4. Gold

Verificare:

- presenza di colonne inverter;
- presenza di colonne piranometri;
- presenza meter solo se configurato;
- valori di irraggiamento medio;
- valori di energia teorica;
- valori di energia netta;
- colonne temporali locali.

### 5. KPI

Verificare:

- frequenza richiesta;
- data di riferimento;
- numero di intervalli attesi;
- soglia di irraggiamento;
- intervalli Contractual Stop;
- valori di availability;
- valori di PR.

---

## Convenzioni importanti

### Timestamp

La colonna temporale di base è:

```text
datetime_utc
```

La gold aggiunge anche:

```text
datetime_local
```

La timezone locale viene letta dalla configurazione.

### Device

I dati silver sono orientati ai dispositivi.

La coppia fondamentale è:

```text
datetime_utc + device_name
```

Questa coppia permette di rappresentare correttamente più dispositivi sulla stessa finestra temporale.

### Potenze ed energie

Nel dominio gold:

- la potenza inverter viene trattata come potenza attiva;
- l'energia inverter viene calcolata moltiplicando potenza per intervallo temporale;
- l'energia netta usa il meter se disponibile;
- se il meter non è disponibile, l'energia netta coincide con la somma delle energie inverter.

### Irraggiamento

L'irraggiamento viene letto dai piranometri e convertito in una media.

Il check di validità usa la soglia:

```text
pr_calculation_parameters.irradiance_threshold_w_m2
```

Solo gli intervalli sopra soglia sono considerati validi per alcune metriche KPI.

---

## Esecuzione locale

Per esecuzioni locali è possibile usare `local_storage_root`.

Esempio:

```python
results = run_solar_pipeline(
    config_path="config/solar_asset.yaml",
    source="meteocontrol",
    stages=["silver", "gold"],
    bucket="local",
    lake_root="solar-dev",
    local_storage_root="./data",
    execution_date="2026-06-01",
)
```

Questo è utile per:

- debug;
- sviluppo di nuove sorgenti;
- validazione della silver;
- test della gold;
- prove sui KPI senza scrivere su storage remoto.

---

## Dipendenze principali

Il pacchetto usa principalmente:

- `pandas`;
- `numpy`;
- `pyyaml`;
- `requests`;
- componenti di storage e pipeline disponibili nel runtime del progetto.

Le dipendenze Airflow non devono entrare nella logica di dominio. Un DAG o un altro orchestratore dovrebbe limitarsi a chiamare il runner passando parametri, credenziali e data di esecuzione.

---

## Principi di manutenzione

Per mantenere il pacchetto leggibile:

- tenere la logica di sorgente dentro `solar/sources/`;
- tenere la logica fotovoltaica dentro `solar/domain/`;
- evitare stringhe di colonne sparse nel codice;
- usare `schemas.py` per nomi colonna condivisi;
- non inserire logica di orchestrazione nei moduli di dominio;
- non inserire logica API nei moduli KPI;
- mantenere il runner come punto di ingresso sottile;
- documentare ogni nuova sezione YAML;
- verificare sempre la compatibilità silver → gold quando si aggiunge una sorgente.

---

## Mappa rapida per nuovi sviluppatori

| Necessità | Modulo da guardare |
|---|---|
| Capire lo YAML | `solar/config/model.py` |
| Aggiungere o validare parametri configurativi | `solar/config/model.py` |
| Capire le colonne attese | `solar/domain/schemas.py` |
| Gestire date, YOM e frequenze KPI | `solar/domain/time.py` |
| Normalizzare dati per device | `solar/domain/transforms.py` |
| Modificare la costruzione gold | `solar/domain/gold.py` |
| Modificare Performance Ratio o availability | `solar/domain/kpi.py` |
| Gestire correnti di stringa | `solar/domain/string_currents.py` |
| Aggiungere una sorgente | `solar/sources/` |
| Cambiare Inaccess | `solar/sources/inaccess.py` |
| Cambiare MeteoControl | `solar/sources/meteocontrol.py` |
| Eseguire la pipeline da codice | `solar/orchestration/runner.py` |

