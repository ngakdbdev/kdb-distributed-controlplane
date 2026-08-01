# Data export

Pull data out of the kdb+ side — HDB history or a live shard's RDB/IDB — and
land it in a cloud lakehouse/warehouse. Extraction to Arrow/Parquet is real and
runs offline; the warehouse loads are coded to the real SDKs and land behind
your account credentials.

## Destinations

| sink | offline? | needs |
|---|---|---|
| **Parquet** | yes | just an output dir — the common interchange, and a real target on its own |
| Snowflake | no | `snowflake-connector-python` + account/user/password/warehouse/database/schema |
| Databricks (Delta) | no | `databricks-sql-connector` + workspace host/http_path/token + a UC catalog/schema/volume |
| Microsoft Fabric | no | `azure-identity` + `azure-storage-file-datalake` + Fabric workspace/lakehouse + a service principal |

Parquet is the interchange all three cloud sinks use: extract → Parquet →
COPY INTO (Snowflake), COPY INTO Delta via a UC volume (Databricks), or write to
OneLake Files and load/shortcut into a Lakehouse table (Fabric). The cloud sinks
refuse with a clear "set THESE vars" message until configured — they never
pretend to have written.

## Source

`--source` is just which q process you point `--host/--port` at:

- **gateway** — recent data across all shards (RDB + IDB), via `.gw`
- a specific shard's **RDB** (today) or **HDB** (history) — point at that host
- HDB history uses a dated partition filter: `--date 2026.08.01`

## Run it

```bash
pip install -r data-plane/export/requirements.txt   # pyarrow + qpython
cd data-plane

# HDB history -> local Parquet (offline, no cloud account)
python -m export.runner --host hdb-s0 --port 5060 \
    --table trade --date 2026.08.01 --symbols AAPL,MSFT \
    --sink parquet --out /tmp/export

# recent data from the gateway -> Snowflake (needs SNOWFLAKE_* env)
export SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... SNOWFLAKE_PASSWORD=... \
       SNOWFLAKE_WAREHOUSE=... SNOWFLAKE_DATABASE=... SNOWFLAKE_SCHEMA=...
python -m export.runner --source gateway --host gateway --port 5050 \
    --table trade --sink snowflake

# list destinations
python -m export.runner --list
```

## Layout & tests

- `schema.py` — kdb→Arrow type mapping + table building (pure, tested)
- `extractor.py` — `build_query` (pure, tested) + batched Arrow extraction
- `job.py` — extractor→sink orchestration (tested with fakes)
- `sinks/` — `parquet` (real, roundtrip-tested) + `snowflake`/`databricks`/`fabric`
- `runner.py` — CLI

```bash
cd data-plane && python -m pytest export/tests
```

The extract→Parquet path is exercised for real; the warehouse SDK calls only run
against a live account, so those aren't in CI — the cloud sinks are tested for
their config/refusal behaviour instead.

## Adding a destination

Subclass `sinks.base.ExportSink` (`open`/`write_table`/`close` + catalog
metadata), register it in `export/__init__.py`, and — if it's credentialed —
raise `SinkNotConfigured` from `open()` when config is missing.

## Natural next step

Trigger exports from the control plane through the fleet agent (increment 3):
add an `export` command the agent runs in the tenant's environment, where their
kdb+ *and* their cloud credentials already live. That keeps warehouse secrets in
the tenant's cluster rather than the SaaS control plane.
