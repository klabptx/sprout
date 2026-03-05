# Sprout - Aurora Knowledge Graph Pipeline (Python)


This is a Python application that generates a knowledge graph model to ground, empower, and define Precision Planting's AI applications (including but not limited to aurora). It builds a small DAG that:

- fetches application data, summary metrics, and diagnostic events from Stitch
- compares record-level metrics against summary averages for each diagnostic event
- groups anomalous metrics into Findings by application type (one Finding per app type)
- enriches findings with event code definitions from `SystemLog.proto` (title, description, recommendation)
- prioritizes findings by severity
- synthesizes a report covering all findings via an LLM (OpenAI by default)

## Knowledge Graph: Events and Findings

The DAG produces a two-layer detection structure in the knowledge graph:

**Events** represent individual anomalous event-code occurrences. Each Event carries:
- the event code (with title from `SystemLog.proto` when available), start/end record, record span
- a severity score (normalized priority)
- a priority score (based on metric co-occurrence, duration, event-code frequency, and spatial clustering)
- a list of anomalous metrics with per-metric deviations from the summary average
- a human-readable summary and a diagnosis prompt

**Findings** aggregate anomalous metrics by application type. Each Finding carries:
- the application id/type/name (fetched from Stitch `/local/applications`)
- metric summaries with peak positive/negative deviations and averages
- event code counts with titles, descriptions, and recommendations from `SystemLog.proto`
- a diagnosis prompt listing anomalous metrics and enriched event code details

An Event can belong to multiple Findings when its anomalous metrics span different application types (many-to-many via `EVENT_SUPPORTS_FINDING` edges). Every application type represented in the run's anomalous metrics gets its own Finding node.

## Pipeline Flow

```
START → parse → analyze_event_records → prioritize → synthesize → END
```

The `synthesize` node collects all findings (sorted by severity, optionally capped to top-N), builds a multi-finding LLM prompt, and generates a report. After the first LLM call, a second LLM call produces a short **operational summary** sentence from task-specific metrics (e.g., "Harvest shows about 9.8 acres at ~2.7 mph with moisture around 21% and dry yield near 223 bu/acre."). 

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,openai]"
```

### Run compare on a local .2020 file

The quickest way to process a Stitch `fs` file end-to-end. The script starts
Stitch, runs the comparison, and tears everything down:

```bash
STITCH_BIN=/path/to/stitch \
STITCH_DATA_DIR=/tmp/stitch \
  scripts/run_compare.sh /path/to/field.2020
```

Results are written to `artifacts/compare_results/`.

To process every `.2020` file in a directory (Stitch restarts per file):

```bash
scripts/run_compare.sh /path/to/dir/
```

### Run the full KG demo

Start Stitch in `fs` mode, then run the pipeline:

```bash
# Terminal 1 – Stitch
/path/to/stitch --port 8888 --data-dir /tmp/stitch fs --file /path/to/field.2020

# Terminal 2 – Demo
export STITCH_LOCAL_BASE_URL=http://localhost:8888
export OPENAI_API_KEY=sk-...
sprout --llm-backend openai
```

### Batch demo pipeline

Run the full LangGraph pipeline for every `.2020` file in a directory and
collect results into a JSONL file. Each line is `{"file": "<name>", "result": {...}}`:

```bash
STITCH_BIN=/path/to/stitch \
LLM_BACKEND=openai \
  scripts/run_demo_batch.sh /path/to/dir/
```

Output defaults to `artifacts/demo_batch_results.jsonl`. Pass a second argument
to override:

```bash
scripts/run_demo_batch.sh /path/to/dir/ results/my_batch.jsonl
```

For a single file:

```bash
scripts/run_demo_batch.sh /path/to/field.2020
```

Environment variables for `scripts/run_demo_batch.sh`:

| Variable | Default | Description |
|---|---|---|
| `STITCH_BIN` | `~/ptx/stitch/.build/arm64/stitch` | Path to Stitch binary |
| `STITCH_PORT` | `8888` | Stitch listen port |
| `STITCH_DATA_DIR` | `/tmp/stitch` | Stitch data directory |
| `LLM_BACKEND` | `openai` | LLM backend for synthesize |
| `SEVERITY_THRESHOLD` | `0.25` | Minimum severity to include in report |

The pipeline runner script (`scripts/run_demo_pipeline.py`) can also be invoked
directly:

```bash
export STITCH_LOCAL_BASE_URL=http://localhost:8888
export LLM_BACKEND=openai
python scripts/run_demo_pipeline.py
```

### Compare + Plot

Write compare results to JSON (one file per stream). Output contains `events`
(individual anomalous event-code occurrences) and `findings` (aggregated by
application type):

```bash
export STITCH_LOCAL_BASE_URL=http://localhost:8888
export COMPARE_OUTPUT_DIR=artifacts/compare_results
PYTHONPATH=. python scripts/compare_event_records.py
```

Optional tuning env vars:

| Variable | Default | Description |
|---|---|---|
| `COMPARE_PCT_THRESHOLD` | `0.25` | Minimum percent deviation to flag a metric |
| `COMPARE_ABS_THRESHOLD` | `0.0` | Minimum absolute deviation (AND with pct) |
| `COMPARE_MAX_EVENTS` | `20` | Cap on events printed to stdout |

Environment variables for `scripts/run_compare.sh`:

| Variable | Default | Description |
|---|---|---|
| `STITCH_BIN` | `~/ptx/stitch/.build/arm64/stitch` | Path to Stitch binary |
| `STITCH_PORT` | `8888` | Stitch listen port |
| `STITCH_DATA_DIR` | `/tmp/stitch` | Stitch data directory |
| `OUTPUT_DIR` | `artifacts/compare_results` | Where JSON results are written |

Interactive explorer (Dash). Provides event scatter plot, findings-by-application-type
table, finding detail view, metric summary, and per-event anomaly drill-down:

```bash
export COMPARE_RESULTS_GLOB="artifacts/compare_results/*.json"
python scripts/plot_compare_dash.py
```

Dashboard env vars:

| Variable | Default | Description |
|---|---|---|
| `COMPARE_DASH_PORT` | `8050` | Dash server port |
| `COMPARE_DASH_USE_CACHE` | `true` | Cache loaded results to disk |
| `COMPARE_DASH_CACHE_PATH` | `artifacts/compare_results/cache.json` | Cache file path |
| `COMPARE_DASH_MAX_ROWS` | `5000` | Max event rows to load (0 = unlimited) |

### LLM backend selection

Use `--llm-backend` to choose where the synthesize step runs:

```bash
sprout --llm-backend openai
sprout --llm-backend local
sprout --llm-backend vllm
sprout --llm-backend lambda
sprout --llm-backend auto    # tries local → vllm → lambda → openai
```

The OpenAI model is configurable via environment variable:

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL=gpt-5.2          # default
export OPENAI_MODEL=gpt-5.2-mini     # cheaper/faster
export OPENAI_MODEL=gpt-4o           # alternative
```

LLM-related env vars:

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | Required for `openai` backend |
| `OPENAI_MODEL` | `gpt-5.2` | OpenAI model to use |
| `LOCAL_MODEL_DIR` / `LOCAL_MODEL_ID` | — | Local HuggingFace model path or ID |
| `LOCAL_ADAPTER_DIR` | — | Optional PEFT adapter directory |
| `LOCAL_BASE_MODEL` | — | Base model when using adapters |
| `LOCAL_MAX_NEW_TOKENS` | `256` | Max tokens for local generation |
| `LOCAL_TEMPERATURE` | `0.2` | Sampling temperature for local models |
| `LAMBDA_API_KEY` | — | Lambda.ai API key |
| `LAMBDA_MODEL` | `lambda_ai/llama3.1-8b-instruct` | Lambda model name |
| `VLLM_BASE_URL` | — | vLLM server base URL |
| `VLLM_MODEL` | — | vLLM model name |

The report output includes the actual model used (e.g. `openai/gpt-5.2`),
which is especially useful in `auto` mode to see which backend succeeded.

### Synthesize configuration

The synthesize node collects all findings sorted by severity and sends them to
the LLM. You can cap the number of findings included in the prompt:

| State field | Default | Description |
|---|---|---|
| `synthesizeMaxFindings` | `-1` | Max findings in LLM prompt (`-1` = all) |

### Operational summary

After the findings summary, the synthesize node makes a second LLM call to
produce a short natural-language sentence describing the run's key operational
metrics. The prompt is built automatically from task-specific metrics detected
in the Stitch data:

| Task | Metrics included |
|---|---|
| **Plant** | Total population, singulation %, seed hybrids (names if 1-2, count if >2) |
| **Harvest** | Acres, speed, moisture %, dry yield |
| **Spray** | Acres, speed, volume per acre |

Example output: *"Seeding shows strong singulation (~99.76%), for total
population (32,800) of FS 6595X RIB and SV Rate."*

If no task can be detected (e.g., only global metrics), the operational summary
is skipped.

### Structured summary

After both LLM calls, the synthesize node fetches a structured summary directly
from Stitch and appends it to the report. This block is **not** processed by
the LLM — it is a verbatim rendering of the per-application metrics returned
by the Stitch `/local/applications` and `/local/metrics/{app_id}` endpoints,
mirroring the output style of the C++ `SummaryWriter*` classes.

The report output is available in the `ReportPayload` in two fields:

- `summary` — the full combined text (findings LLM + operational sentence)
- `operational_summary` — the LLM-generated operational sentence alone (empty
  string if no task detected or LLM failed)

### Event code definitions

If `SystemLog.proto` is present at the repo root, event code definitions are
automatically parsed and incorporated into Event and Finding nodes. Each event
code's title, description, and recommendation (when available) enrich the
diagnosis prompts that feed into the LLM.

Override the proto file location:

```bash
export EVENT_PROTO_PATH=/path/to/SystemLog.proto   # default: SystemLog.proto
```

## Generate diagrams / exports

> **Note:** diagram and KG export scripts are not yet migrated to the `sprout` package.

## Files

- `SystemLog.proto`: Protobuf event code definitions with titles, descriptions, and recommendations.
- `sprout/config.py`: Settings (pydantic-settings), `get_settings()` singleton. All env vars declared here.
- `sprout/graph_types.py`: Typed payloads (`EventPayload`, `FindingPayload`, etc.) and KG envelope definition.
- `sprout/graph.py`: Graph definition and routing. `analyze_event_records` creates Event and Finding nodes; downstream nodes prioritize and synthesize.
- `sprout/llm_backends.py`: LLM backend dispatch (OpenAI, local, vLLM, Lambda) with configurable models.
- `sprout/cli.py`: CLI entry point (`sprout` command). Prints the report and LLM model used.
- `sprout/nodes/`: Individual pipeline node implementations (`parse`, `analyze`, `prioritize`, `augment`, `synthesize`).
- `sprout/kg/utils.py`: Anomaly detection, finding-accumulation, and proto event-code parsing logic.
- `sprout/kg/structured_summary.py`: Fetches per-application metrics from Stitch and builds the operational LLM prompt.
- `scripts/compare_event_records.py`: Standalone script that runs record-level metric comparison and outputs JSON with `events` and `findings`.
- `scripts/run_compare.sh`: Runs Stitch + compare for a single `.2020` file or a directory of them.
- `scripts/run_demo_pipeline.py`: Runs the full LangGraph pipeline once and outputs a JSON record to stdout.
- `scripts/run_demo_batch.sh`: Batch orchestrator that runs the pipeline for a directory of `.2020` files and collects results into a JSONL file.
- `scripts/plot_compare_dash.py`: Dash interactive explorer for compare results.

## Notes

- All data is fetched directly from Stitch `/local/*` endpoints.
- The segmentation and time-series components still exist, but are not wired into
  the main DAG flow yet.
- Events carry per-record anomalous metric comparisons; Findings aggregate those
  metrics by application type with a diagnosis prompt.
- Metric-to-application-type mapping is fetched from Stitch at runtime
  (`/local/applications` and `/local/metrics/{app_id}`).
- The augment/RAG step is wired into the pipeline via conditional edge as it is currently a stubbed placeholder. It can
  be re-enabled by adding conditional edges back in `build_graph()`.
- GIS fields exist but are null to keep the artifact shape future-ready.
