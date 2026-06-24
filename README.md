# Pacchetto `solar`

`solar` contiene le componenti riusabili per acquisire e normalizzare dati di impianto da sorgenti supportate e per calcolare KPI a partire da un dataset gold già costruito.

In questa versione la **fase gold non è più implementata nel pacchetto**. La costruzione gold contiene regole operative molto specifiche del progetto, per esempio soglie di irraggiamento, calcolo dell'energia teorica, scelta tra energia da meter o da inverter, rinomina delle colonne e logiche di riempimento dei valori mancanti. Per questo motivo deve vivere nel DAG, o comunque nel job orchestratore che conosce il contesto applicativo.

---

## Responsabilità del pacchetto

Il pacchetto mantiene queste responsabilità:

- lettura e validazione della configurazione YAML;
- costruzione del contesto di esecuzione;
- client e adapter per sorgenti dati supportate;
- normalizzazione bronze → silver;
- utility temporali e trasformazioni riusabili;
- calcolo KPI a partire da una tabella gold prodotta esternamente;
- runner per eseguire bronze e silver da script, notebook o DAG.

Il pacchetto **non** mantiene più queste responsabilità:

- costruzione del dataset gold;
- regole di enrichment specifiche dell'impianto;
- scelte progettuali locali su NaN, meter, soglie o colonne gold finali.

---

## Flusso consigliato

```text
configurazione impianto
        ↓
bronze  ── gestita dal pacchetto solar + data_platform
        ↓
silver  ── gestita dal pacchetto solar + data_platform
        ↓
gold    ── gestita dal DAG/job applicativo
        ↓
KPI     ── calcolabili con SolarKpiProcessor
```

Il pacchetto `data_platform` resta il livello generico infrastrutturale: datalake, storage, pipeline bronze/silver/gold astratte e utility comuni.

Il pacchetto `solar` fornisce adapter, configurazione e KPI. La gold viene iniettata dal DAG usando la `GoldPipeline` generica di `data_platform` con un processor locale al DAG.

---

## Struttura del pacchetto

```text
solar/
├── __init__.py
├── config/
│   ├── __init__.py
│   └── model.py
├── domain/
│   ├── __init__.py
│   ├── schemas.py
│   ├── time.py
│   ├── transforms.py
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

Non è più presente `solar/domain/gold.py`.

---

## Configurazione

La configurazione viene letta da un file YAML unico tramite:

```python
from solar.config import load_solar_config

config = load_solar_config("config/limes_6.yaml", source="inaccess")
```

La funzione restituisce una `SolarConfig`, cioè una vista tipizzata della configurazione grezza.

Campi principali richiesti:

```yaml
metadata:
  plant_name: limes_6
  timezone: Europe/Rome
  PAC_date: 2025-02-01
  OM_date: 2025-02-01
  plant_nominal_power_kw: 8541.45

pr_calculation_parameters:
  irradiance_threshold_w_m2: 50
  degradation_factor: 0.004
  time_interval_hours: 0.25
  has_meter: true

connections:
  inverters: []
  pyranometers: []
  meters: []
```

---

## Bronze e silver

Il runner principale è:

```python
from solar.orchestration import run_solar_pipeline

run_solar_pipeline(
    config_path="config/limes_6.yaml",
    source="inaccess",
    stages=["bronze", "silver"],
    bucket="data-monitoring-platform",
    lake_root="solar-plants-dev",
    connection_config={"api_key": "..."},
    storage=storage,
)
```

Sono supportati solo gli stage `bronze` e `silver`.

Se viene richiesto `stages=["gold"]`, il runner solleva un errore esplicito: la gold deve essere implementata nel DAG/job applicativo.

---

## KPI

I KPI restano nel pacchetto perché rappresentano una logica riusabile a valle di una tabella gold già prodotta.

Uso tipico:

```python
from solar.domain.kpi import SolarKpiProcessor

processor = SolarKpiProcessor()
df_kpi = processor.calculate(
    df=df_gold,
    config=config,
    freq="day",
    year=2026,
    month=6,
    day=23,
    cs_intervals=[],
)
```

La tabella gold passata al processor deve contenere le colonne canoniche attese, definite in `solar/domain/schemas.py`.

---

## Dove mettere la gold

La gold deve stare nel DAG o in un modulo locale del progetto Airflow, non in `solar`.

Esempio architetturale:

```python
from data_platform.pipelines import GoldPipeline
from solar.config import build_solar_context, load_solar_config
from solar.sources import get_source_adapter

config = load_solar_config("config/limes_6.yaml", source="inaccess")
context = build_solar_context(config, source="inaccess")
adapter = get_source_adapter("inaccess", lake_root="solar-plants-dev")

GoldPipeline(lake).run(
    context=context,
    processor=DagSolarGoldProcessor(adapter),
)
```

`DagSolarGoldProcessor` è una classe locale al DAG e contiene le regole specifiche del progetto.

---

## Convenzione progettuale

- Se una logica serve a leggere una sorgente o normalizzare dati raw/silver, può stare nel pacchetto.
- Se una logica decide come costruire la gold di uno specifico progetto, deve stare nel DAG/job.
- Se una logica calcola KPI standard su una gold già pronta, può stare nel pacchetto.
- Se una logica dipende fortemente da un cliente, un impianto o una richiesta operativa, deve stare fuori dal pacchetto.

Questa separazione evita che `solar` diventi un pacchetto troppo legato a un singolo caso d'uso.
