# Sprout — Knowledge Graph Architecture Code Walkthrough
## !!!BEWARE!!!
### Walkthrough was vibecoded with claude. Still working through edits and updates. May contain out-of-date info, "inferred" functionality, and general AI tomfoolery.

A technical walkthrough of the `sprout` codebase. Covers data ingestion, the LangGraph pipeline, anomaly detection logic, knowledge graph construction, and LLM synthesis.

---

## Table of Contents

1. [Overview](#overview)
2. [Project Structure](#project-structure)
3. [Entry Point](#entry-point)
4. [Data Sources](#data-sources)
   - [Tailor Stream API](#tailor-stream-api)
   - [Stitch File System API](#stitch-file-system-api)
   - [SystemLog.proto](#systemlogproto)
5. [LangGraph DAG](#langgraph-dag)
   - [GraphState — shared state](#graphstate--shared-state)
   - [Graph Assembly](#graph-assembly)
6. [Node: parse()](#node-parse)
7. [Node: analyze\_event\_records()](#node-analyze_event_records)
   - [Grouping events by code](#step-1-group-events-by-code)
   - [Spatial clustering boost](#step-2-spatial-clustering-boost)
   - [Record metric comparison](#step-3-record-metric-comparison)
   - [Priority scoring](#step-4-priority-scoring)
   - [Building Event nodes](#step-5-building-event-nodes)
   - [Building Finding nodes](#step-6-building-finding-nodes)
8. [Node: prioritize()](#node-prioritize)
9. [Node: synthesize()](#node-synthesize)
   - [Prompt construction](#prompt-construction)
   - [LLM backend dispatch](#llm-backend-dispatch)
   - [Report node](#report-node)
10. [Knowledge Graph Schema](#knowledge-graph-schema)
    - [NodeEnvelope](#nodeenvelope)
    - [Node types](#node-types)
    - [Edge types](#edge-types)
11. [Key Data Models](#key-data-models)
12. [Configuration Reference](#configuration-reference)
13. [Architecture Notes](#architecture-notes)

---

## Overview

Sprout is an diagnostic pipeline that turns raw data from 2020 files into a structured **knowledge graph (KG)** and then synthesizes the findings into a plain-language report via an LLM.

The pipeline has four stages:

```
parse()  →  analyze_event_records()  →  prioritize()  →  synthesize()
```

Each stage is implemented as an async node in a [LangGraph](https://github.com/langchain-ai/langgraph) `StateGraph`. Stages accumulate knowledge graph nodes into a shared `GraphState` dict using merge reducers — no node overwrites another's output.

---

## Project Structure

```
sprout/
├── src/demo/
│   ├── graph.py           # LangGraph nodes + DAG wiring
│   ├── graph_types.py     # TypedDicts: GraphState, NodeEnvelope, all payloads
│   ├── llm_backends.py    # LLM dispatch: local HF / vLLM / Lambda.ai / OpenAI
│   ├── kg_export.py       # KG → JSON / Graphviz DOT export
│   └── demo.py            # CLI entry point (single run)
├── scripts/
│   ├── run_demo_pipeline.py   # Primary pipeline runner (outputs JSON to stdout)
│   ├── tailor_shim.py         # HTTP shim: Tailor-shaped API over Stitch
│   └── plot_compare_dash.py   # Interactive Dash visualization
├── utils.py               # Anomaly detection, finding accumulation, proto parsing
└── SystemLog.proto        # Event code definitions (title, description, recommendation)
```

---

## Entry Point

`scripts/run_demo_pipeline.py` builds the graph and invokes it with a default initial state:

```python
from src.demo.graph import build_graph, default_state

graph = build_graph()
result = asyncio.run(graph.ainvoke(default_state()))
```

The full post-run state (including the entire KG) is written as JSON to stdout. `kg_export.py` can also render it as a Graphviz DOT diagram.

---

## Data Sources

### Tailor Stream API

The pipeline's primary data source. Fetched in `parse()` via `_fetch_tailor_stream()`.

**Environment variables:**

| Variable | Description |
|---|---|
| `TAILOR_STREAM_URL` | Direct endpoint URL (takes precedence) |
| `TAILOR_BASE_URL` + `TAILOR_ORG_CODE` + `TAILOR_STREAM_ID` | Composed URL fallback |

**Response shape (simplified):**

```json
{
  "currentStream": {
    "streamId": "abc123",
    "overview": { "streamId": "abc123", ... },
    "summaryData": [
      {
        "applicationId": "row_unit_1",
        "metrics.downforce_p95": 123.4,
        "metrics.singulation_pct": 89.2,
        ...
      }
    ],
    "diagnostics": [
      {
        "codes": {
          "1001": { "count": 5 },
          "1042": { "count": 2 }
        }
      }
    ],
    "diagnosticEventDetails": [
      {
        "eventCode": 1001,
        "start_record": 150,
        "end_record": 155,
        "location": [{ "lat": 40.1234, "lon": -88.4567 }]
      }
    ],
    "contextStreams": [ ... ]
  }
}
```

The pipeline stores these four lists verbatim in `GraphState` as `tailorSummary`, `tailorDiagnostics`, `tailorEventDetails`, and `tailorContext`.

---

### Stitch File System API

A local HTTP service (`STITCH_LOCAL_BASE_URL`, default `http://localhost:8888`) that exposes equipment metrics from the on-disk file system. Used in `analyze_event_records()` for two purposes:

1. **Metric metadata** — mapping metric keys to application types/names
2. **Per-record values** — fetching the actual sensor reading at a specific record index

| Endpoint | Returns |
|---|---|
| `GET /local/applications?l=en-US` | `[{ applicationId, name, type.key, type.name }]` |
| `GET /local/metrics/{app_id}` | `[{ key, name }]` — metric key → human name |
| `GET /local/metrics/{app_id}?record={N}` | `{ metrics: [{ value: { implement_average: float } }] }` |

`build_metric_to_app_map()` calls the first two endpoints to build a `dict[metric_key → { application_id, application_type, application_name, metric_name }]` lookup used when constructing Finding nodes.

> **Example metric map**
> ```python
> {
>     "metrics.vacuum_right": {
>         "application_id":   "row_unit_1",
>         "application_type": "seeding",
>         "application_name": "Seeder",
>         "metric_name":      "Vacuum Right",
>     },
>     "metrics.vacuum_left": {
>         "application_id":   "row_unit_1",
>         "application_type": "seeding",
>         "application_name": "Seeder",
>         "metric_name":      "Vacuum Left",
>     },
>     "metrics.seed_spacing": {
>         "application_id":   "row_unit_1",
>         "application_type": "seeding",
>         "application_name": "Seeder",
>         "metric_name":      "Seed Spacing",
>     },
>     "metrics.downforce_avg": {
>         "application_id":   "row_unit_1",
>         "application_type": "row_cleaner",
>         "application_name": "Row Cleaner",
>         "metric_name":      "Downforce Average",
>     },
> }
> ```

---

### SystemLog.proto

A protobuf schema file that defines named event codes and their human-readable metadata. Parsed once at startup by `parse_proto_event_codes()` in `utils.py`.

**Environment variable:** `EVENT_PROTO_PATH` (default: `SystemLog.proto`)

The resulting `event_code_defs` dict is passed to `build_findings_by_app_type()` and used to enrich LLM prompts with code titles, descriptions, and recommended actions.

> **Example: event code definitions for codes used in a real run** 
```
  // SRM

  SRM_SEED_SENSOR_OBSTRUCTION = 10015
  [
    (option_event_definition) =
    {
      instantaneous: false,
      audible_alarm : EVENT_AUDIO_NONE,
      display_warning : false,
      title : "Seed Sensor Obstruction",
      description : "An obstruction has been detected on the seed sensor.",
      recommendation : "Inspect SpeedTube to look for a wedged seed or other obstruction near the seed sensor inside the belt housing."
    }
  ];
```

## LangGraph DAG

### GraphState — shared state

All pipeline nodes read from and write to a single `GraphState` TypedDict defined in `graph_types.py`. LangGraph applies **reducer functions** when merging partial updates returned by each node — no node can accidentally overwrite another's output.

```python
# graph.py — reducer definitions
def merge_dict(
    left: dict[str, NodeEnvelope],
    right: dict[str, NodeEnvelope]
) -> dict[str, NodeEnvelope]:
    merged = dict(left)
    merged.update(right)
    return merged

def concat(left: list[str], right: list[str]) -> list[str]:
    return left + right
```

```python
# graph_types.py
class GraphState(TypedDict):
    # ── Raw inputs from Tailor ──────────────────────────────────────────────
    tailorSummary: list[dict]          # summaryData[0] — per-app metric averages
    tailorDiagnostics: list[dict]      # aggregate event code counts
    tailorEventDetails: list[dict]     # individual event occurrences with records/GPS
    tailorOverview: dict | None        # stream metadata
    tailorContext: list[dict]          # supplemental context streams

    # ── Knowledge graph (merge-reducer accumulation) ────────────────────────
    kg: Annotated[KG, merge_dict]                          # node_id → NodeEnvelope
    segmentIds:       Annotated[list[str], concat]         # (reserved, not currently used)
    featureIds:       Annotated[list[str], concat]         # (reserved, not currently used)
    eventIds:         Annotated[list[str], concat]
    findingIds:       Annotated[list[str], concat]
    priorityIds:      Annotated[list[str], concat]
    augmentationIds:  Annotated[list[str], concat]
    recommendationIds: Annotated[list[str], concat]
    reportId: str

    # ── Run identifiers ─────────────────────────────────────────────────────
    runId: str
    sourceFile: str                    # e.g. "{runId}.2020"

    # ── Scoring + quality signals ───────────────────────────────────────────
    topSeverity: float
    topFindingId: str | None
    dataQualityFlag: bool              # True if no summaryData was present

    # ── Pipeline configuration ──────────────────────────────────────────────
    severityThreshold: float           # gate for conditional augment routing
    synthesizeMaxFindings: int         # cap on findings sent to LLM (-1 = all)
    demo: bool
    demoController: "DemoController | None"

    # ── LLM ────────────────────────────────────────────────────────────────
    llmBackend: str                    # "auto" | "local" | "vllm" | "lambda" | "openai"
    llmModel: str                      # resolved model label, e.g. "openai/gpt-5.2"
    llmError: str | None
```

`KG` is a type alias for `dict[str, NodeEnvelope]`. Every node returns a *partial* dict — only the keys it updates — and LangGraph merges them using the annotated reducers.

---

### Graph Assembly

`build_graph()` in `graph.py` wires the nodes together. The current pipeline is strictly linear:

```python
def build_graph() -> Callable[[GraphState], GraphState]:
    builder: StateGraph = StateGraph(GraphState)

    builder.add_node("parse",                 with_demo("parse",                 parse))
    builder.add_node("analyze_event_records", with_demo("analyze_event_records", analyze_event_records))
    builder.add_node("prioritize",            with_demo("prioritize",            prioritize))
    builder.add_node("synthesize",            with_demo("synthesize",            synthesize))

    builder.add_edge(START,                    "parse")
    builder.add_edge("parse",                  "analyze_event_records")
    builder.add_edge("analyze_event_records",  "prioritize")
    builder.add_edge("prioritize",             "synthesize")
    builder.add_edge("synthesize",             END)

    return builder.compile()
```

**Note on `augment()`:** A fifth node (`augment()`, graph.py:604) is defined along with a conditional router:

```python
def route_after_prioritize(state: GraphState) -> str:
    return "augment" if state["topSeverity"] >= state["severityThreshold"] else "synthesize"
```

This routing is **not currently wired** — `add_edge("prioritize", "synthesize")` bypasses it. When enabled, high-severity runs would first pass through `augment()` to attach RAG snippets and Recommendation nodes before synthesis.

`with_demo()` is a thin wrapper that, when `state["demo"]` is True, pauses execution between nodes so a UI can display intermediate results. In production it's a no-op passthrough.

---

## Node: parse()

**File:** `graph.py:277`
**Purpose:** Fetch the Tailor stream and create the root `Run` KG node.

```python
async def parse(_: GraphState) -> dict:
    payload = _fetch_tailor_stream()
    current = payload.get("currentStream", {})
    overview = current.get("overview")
    summary_data = current.get("summaryData", []) or []
    diagnostics = current.get("diagnostics", []) or []
    event_details = current.get("diagnosticEventDetails", []) or []

    run_id = current.get("streamId") or (overview.get("streamId") if overview else None)
    run_id = run_id or new_id("run")
    source_file = f"{run_id}.2020"

    samples: list[Sample] = []
    run_node = build_run_node(run_id, samples)

    return {
        "samples": samples,
        "runId": run_id,
        "sourceFile": source_file,
        "tailorSummary": summary_data,
        "tailorDiagnostics": diagnostics,
        "tailorEventDetails": event_details,
        "tailorOverview": overview,
        "tailorContext": payload.get("contextStreams", []) or [],
        "kg": {run_node["node_id"]: run_node},
        "dataQualityFlag": False if summary_data else True,
    }
```

**Outputs to state:**

- `kg` — one `Run` node keyed by `run_id`
- `runId`, `sourceFile` — run identifiers used by downstream nodes
- `tailorSummary`, `tailorDiagnostics`, `tailorEventDetails`, `tailorContext` — raw Tailor data for `analyze_event_records()`
- `dataQualityFlag` — set to `True` if `summaryData` is empty, which later reduces the LLM confidence score

> **Example: Run node payload**
> ```json
> {
>   "node_id":   "abc123",
>   "node_type": "Run",
>   "payload": {
>     "run_id":         "abc123",
>     "sample_rate_hz": 5,
>     "start_time_s":   0,
>     "end_time_s":     0,
>     "spatial_ref":    null
>   },
>   "edges": []
> }
> ```

---

## Node: analyze\_event\_records()

**File:** `graph.py:304`
**Purpose:** Detect anomalous event-code occurrences, score them by priority, and build `Event` and `Finding` KG nodes.

This is the most complex node. It runs in six logical phases.

---

### Step 1: Group events by code

Each entry in `tailorEventDetails` is an individual occurrence of a diagnostic event code. They are grouped by integer code to support frequency counting and spatial analysis:

```python
grouped: dict[str, list[dict]] = defaultdict(list)
for event in event_details:
    code = event.get("eventCode")
    start_record = event.get("start_record")
    if code is None or start_record is None:
        continue
    grouped[str(code)].append(event)
```

Per-code counts are also extracted from `tailorDiagnostics[0]["codes"]`, which is an aggregate from the Tailor stream:

```python
code_counts: dict[str, int] = {}
if diagnostics:
    codes_map = diagnostics[0].get("codes", {})
    for code_str, info in codes_map.items():
        code_counts[code_str] = info.get("count", 1) if isinstance(info, dict) else 1
```

---

### Step 2: Spatial clustering boost

GPS coordinates from each event are rounded to 4 decimal places to create a grid cell. Cells with multiple events get a boost factor applied during priority scoring:

```python
spatial_counts: Counter = Counter()
for events in grouped.values():
    for event in events:
        cell = spatial_cell(event)   # → (round(lat, 4), round(lon, 4)) | None
        if cell is not None:
            spatial_counts[cell] += 1
```

This means events that repeatedly fire in the same field area are flagged more aggressively than isolated occurrences.

---

### Step 3: Record metric comparison

For each event occurrence, the pipeline fetches the sensor readings at the event's `start_record` from the Stitch API and compares them against the run-level summary averages from `tailorSummary`:

```python
record_metrics = await asyncio.to_thread(
    load_record_metrics, application_id, start_record
)
anomalies = compare_metrics(summary_metrics, record_metrics)
```

`compare_metrics()` in `utils.py`:

```python
def compare_metrics(
    summary: dict[str, float],
    record: dict[str, float],
    pct_threshold: float = COMPARE_PCT_THRESHOLD,   # default 0.25 (25%)
    abs_threshold: float = COMPARE_ABS_THRESHOLD,   # default 0.0
) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []
    for key, summary_val in summary.items():
        if key not in record:
            continue
        record_val = record[key]
        abs_delta = record_val - summary_val
        if summary_val == 0:
            # Skip — percent deviation undefined for zero averages
            continue
        pct_delta = abs_delta / abs(summary_val)
        if abs(pct_delta) >= pct_threshold and abs(abs_delta) >= abs_threshold:
            anomalies.append({
                "metric":    key,
                "summary":   summary_val,
                "record":    record_val,
                "pct_delta": pct_delta,
                "abs_delta": abs_delta,
            })
    return anomalies
```

An anomaly is flagged when **both** thresholds are met:
- `|pct_delta| ≥ COMPARE_PCT_THRESHOLD` (percentage deviation)
- `|abs_delta| ≥ COMPARE_ABS_THRESHOLD` (absolute deviation)

Events with zero anomalies are discarded immediately.

**Environment variables:** `COMPARE_PCT_THRESHOLD` (default `0.25`), `COMPARE_ABS_THRESHOLD` (default `0.0`)

> **Example: anomaly list for a real event**
> ```json
> [
>   {
>     "metric":    "metrics.vacuum_right",
>     "summary":   0.001,
>     "record":    10.023,
>     "pct_delta": 10022.0,
>     "abs_delta": 10.022
>   },
>   {
>     "metric":    "metrics.vacuum_left",
>     "summary":   0.004,
>     "record":    1.031,
>     "pct_delta": 256.75,
>     "abs_delta": 1.027
>   },
>   {
>     "metric":    "metrics.seed_spacing",
>     "summary":   5.823,
>     "record":    3.371,
>     "pct_delta": -0.421,
>     "abs_delta": -2.452
>   }
> ]
> ```

---

### Step 4: Priority scoring

Each event with at least one anomaly gets a priority score, which drives node ordering and severity normalization.

```python
def compute_priority(
    anomalies: list[dict[str, Any]],
    event: dict,
    code_count: int,
    spatial_counts: Counter,
) -> float:
    if not anomalies:
        return 0.0

    co = len(anomalies)
    co_score = co ** 2 * max(abs(a["pct_delta"]) for a in anomalies)

    span = record_span(event)                        # end_record - start_record
    duration_weight = 1.0 + math.log1p(span)

    frequency_factor = math.log2(1 + code_count)

    cell = spatial_cell(event)
    spatial_boost = 1.0
    if cell is not None and spatial_counts[cell] > 1:
        spatial_boost = min(2.0, spatial_counts[cell] / 3)

    return co_score * duration_weight * frequency_factor * spatial_boost
```

**Formula:**

```
priority = co_score × duration_weight × frequency_factor × spatial_boost

  co_score         = len(anomalies)² × max(|pct_delta|)
  duration_weight  = 1.0 + log(1 + record_span)
  frequency_factor = log₂(1 + code_count)
  spatial_boost    = min(2.0, cell_count / 3)   [1.0 if not clustered]
```

After scoring all events, results are sorted descending and the top `COMPARE_MAX_EVENTS` (default 20) are retained. Severity is then normalized across the retained set:

```python
max_priority = max(top_results[0][0], 1e-9)
# later, per event:
severity = round(min(1.0, priority / max_priority), 2)
```

**Environment variable:** `COMPARE_MAX_EVENTS` (default `20`)

> **Example: top-5 events with their priority scores and normalized severity**
>
> | Rank | Code  | Record | Span | Anomalies | Priority    | Severity |
> |------|-------|--------|------|-----------|-------------|----------|
> | 1    | 10015 | 4821   | 3    | 3         | 604 812.4   | 1.00     |
> | 2    | 10015 | 5103   | 2    | 3         | 462 205.1   | 0.76     |
> | 3    | 10015 | 4612   | 4    | 3         | 330 891.6   | 0.55     |
> | 4    | 10015 | 5388   | 1    | 3         | 294 317.8   | 0.49     |
> | 5    | 10015 | 4290   | 3    | 2         | 241 503.2   | 0.40     |
>
> All 6 events are the same code (10015). Priority is high because `code_count = 6` drives `frequency_factor = log₂(7) ≈ 2.81` and `max(|pct_delta|) ≈ 10 022` (vacuum_right near zero baseline).

---

### Step 5: Building Event nodes

For each retained event, a KG `Event` node is created. The `diagnosis_prompt` field is a structured string that will later be embedded in the LLM prompt:

```python
ecd = event_code_defs.get(int(code))
code_label = f"{code} ({ecd['title']})" if ecd and ecd.get("title") else str(code)

event_payload: EventPayload = {
    "event_id":      event_id,
    "run_id":        state["runId"],
    "event_code":    int(code),
    "application_id": application_id,
    "start_record":  start_record,
    "end_record":    int(end_record) if end_record is not None else None,
    "event_length":  span,
    "severity":      severity,
    "priority_score": priority,
    "anomalies":     anomalies,
    "summary":       event_summary,      # human-readable one-liner
    "diagnosis_prompt": (
        f"Event code {code_label} at record {start_record}"
        + (f" (span {span} records)" if span else "")
        + f": {len(anomalies)} anomalous metrics. "
        + "; ".join(
            f"{a['metric']}: record={a['record']:.3f}, summary={a['summary']:.3f}, "
            f"deviation={a['pct_delta']:+.0%}"
            for a in top_anomalies[:6]
        )
        + "."
    ),
    "evidence_refs": [f"record:{application_id}:{start_record}"],
}
```

> **Example: full EventPayload JSON for one event**
> ```json
> {
>   "event_id":        "evt_0001",
>   "run_id":          "abc123",
>   "event_code":      10015,
>   "application_id":  "row_unit_1",
>   "start_record":    4821,
>   "end_record":      4824,
>   "event_length":    3,
>   "severity":        1.0,
>   "priority_score":  604812.4,
>   "anomalies": [
>     {"metric": "metrics.vacuum_right", "summary": 0.001, "record": 10.023,
>      "pct_delta": 10022.0, "abs_delta": 10.022},
>     {"metric": "metrics.vacuum_left",  "summary": 0.004, "record":  1.031,
>      "pct_delta":   256.75, "abs_delta":  1.027},
>     {"metric": "metrics.seed_spacing", "summary": 5.823, "record":  3.371,
>      "pct_delta":    -0.421, "abs_delta": -2.452}
>   ],
>   "summary": "Event code 10015 (Seed Sensor Obstruction) @ record 4821: 3 anomalous metrics. metrics.vacuum_right=10.023 (expected 0.001, +1002300%); metrics.vacuum_left=1.031 (expected 0.004, +25675%); metrics.seed_spacing=3.371 (expected 5.823, -42%).",
>   "diagnosis_prompt": "Event code 10015 (Seed Sensor Obstruction) at record 4821 (span 3 records): 3 anomalous metrics. metrics.vacuum_right: record=10.023, summary=0.001, deviation=+1002300%; metrics.vacuum_left: record=1.031, summary=0.004, deviation=+25675%; metrics.seed_spacing: record=3.371, summary=5.823, deviation=-42%.",
>   "evidence_refs": ["record:row_unit_1:4821"]
> }
> ```

---

### Step 6: Building Finding nodes

Events are aggregated into **Findings** by application type — one Finding per distinct application type seen across all anomalous metrics. This is handled by `build_findings_by_app_type()` in `utils.py`.

```python
def build_findings_by_app_type(
    events: list[dict[str, Any]],
    metric_to_app: dict[str, dict[str, str]],
    fallback_application_id: str,
    event_code_defs: dict[int, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """
    Each event dict requires: event_id, event_code, severity, anomalies.
    Returns plain finding dicts sorted by severity descending.
    """
    app_acc: dict[str, dict[str, Any]] = {}

    for evt in events:
        for anomaly in evt.get("anomalies", []):
            metric_key = anomaly["metric"]
            app_info = metric_to_app.get(metric_key) or {
                "application_id":   fallback_application_id,
                "application_type": "unknown",
                "application_name": "Unknown",
                "metric_name":      metric_key.removeprefix("metrics."),
            }
            type_key = app_info["application_type"]
            # accumulate per-metric peak deviations, event refs, code counts...
```

For each accumulated application type, a `diagnosis_prompt` is built by combining:
- Per-metric peak positive/negative deviations and event counts
- Per-event-code counts, enriched with proto titles, descriptions, and recommendations

```
{application_name} Findings:
Anomalous metrics detected:
{metric_name}: +{peak_pos}% / {peak_neg}%, avg {tailor_average} ({event_count} events)
...

Event codes:
{code}: {title} ({count})
    {description}
    Recommendation: {recommendation}
...
```

This `diagnosis_prompt` is stored directly in the `FindingPayload` and injected into the LLM prompt at synthesis time.

After `build_findings_by_app_type()` returns, `analyze_event_records()` wraps each plain finding dict into a KG `Finding` node and wires `EVENT_SUPPORTS_FINDING` edges from each constituent Event:

```python
for evt_id, f_ids in event_to_findings.items():
    kg_updates[evt_id]["edges"] = [
        {"type": "EVENT_SUPPORTS_FINDING", "to": f_id} for f_id in f_ids
    ]
```

**Returns to state:** `eventIds`, `findingIds`, `kg` (Event + Finding nodes)

> **Example: FindingPayload JSON for one application type**
> ```json
> {
>   "finding_id": "find_0008",
>   "run_id": "abc123",
>   "application_id": "row_unit_1",
>   "application_type": "seeding",
>   "application_name": "Seeder",
>   "title": "Seeder anomaly findings",
>   "severity": 1.0,
>   "event_refs": ["evt_0001", "evt_0002", "evt_0003", "evt_0004", "evt_0005", "evt_0006"],
>   "metric_summaries": [
>     {"metric_key": "metrics.vacuum_right", "metric_name": "Vacuum Right",
>      "peak_pct_pos": 999.0, "peak_pct_neg": 0.0, "tailor_average": 0.001, "event_count": 6},
>     {"metric_key": "metrics.vacuum_left",  "metric_name": "Vacuum Left",
>      "peak_pct_pos": 256.5, "peak_pct_neg": 0.0, "tailor_average": 0.004, "event_count": 6},
>     {"metric_key": "metrics.seed_spacing", "metric_name": "Seed Spacing",
>      "peak_pct_pos": 0.0,   "peak_pct_neg": -0.42, "tailor_average": 5.823, "event_count": 4}
>   ],
>   "event_code_counts": {"10015": 6},
>   "diagnosis_prompt": "..."
> }
> ```

> **Example: diagnosis_prompt string passed to LLM for a real finding**
> ```
> Seeder Findings:
> Anomalous metrics detected:
> Vacuum Right: +99900% / +0%, avg 0.001 (6 events)
> Vacuum Left: +25650% / +0%, avg 0.004 (6 events)
> Seed Spacing: +0% / -42%, avg 5.823 (4 events)
>
> Event codes:
> 10015: Seed Sensor Obstruction (6)
>     An obstruction has been detected on the seed sensor.
>     Recommendation: Inspect SpeedTube to look for a wedged seed or other obstruction near the seed sensor inside the belt housing.
> ```

---

## Node: prioritize()

**File:** `graph.py:493`
**Purpose:** Create a `Priority` node for each Finding, tracking the highest-severity finding.

```python
async def prioritize(state: GraphState) -> dict:
    priority_ids: list[str] = []
    top_severity = 0.0
    top_finding_id: str | None = None
    kg_updates: KG = {}

    for finding_id in state["findingIds"]:
        finding_node = state["kg"].get(finding_id)
        if not finding_node:
            continue

        score = round(float(finding_node["payload"]["severity"]), 2)
        priority_id = new_id("prio")

        priority_payload: PriorityPayload = {
            "priority_id": priority_id,
            "finding_id":  finding_id,
            "score":       score,
            "rationale":   f"Severity {score} derived from prevalence and magnitude.",
        }
        kg_updates[priority_id] = {
            "node_id":   priority_id,
            "node_type": "Priority",
            "payload":   priority_payload,
            "edges":     [],
        }

        kg_updates[finding_id] = with_edges(
            finding_node,
            [{"type": "FINDING_HAS_PRIORITY", "to": priority_id}],
        )

        if score > top_severity:
            top_severity = score
            top_finding_id = finding_id

    return {
        "priorityIds":   priority_ids,
        "topSeverity":   top_severity,
        "topFindingId":  top_finding_id,
        "kg":            kg_updates,
    }
```

The `topSeverity` and `topFindingId` scalars are used by:
- `route_after_prioritize()` — for conditional augment routing (currently bypassed)
- `synthesize()` — to compute the report's `confidence` score

**Returns to state:** `priorityIds`, `topSeverity`, `topFindingId`, `kg` (Priority nodes + updated Finding edges)

---

## Node: synthesize()

**File:** `graph.py:662`
**Purpose:** Collect all Findings, build an LLM prompt, call the configured LLM backend, and emit the final `Report` KG node.

---

### Prompt construction

`_build_llm_prompt()` assembles the prompt from the sorted Finding nodes. Each finding's `diagnosis_prompt` (already enriched with proto event code metadata) is embedded directly:

```python
def _build_llm_prompt(
    findings: list[NodeEnvelope],
    top_severity: float,
    event_summaries: list[str],
    recommendation_texts: list[str],
    confidence: float,
    run_id: str = "",
) -> str:
    source_line = f"Source file: {run_id}\n" if run_id else ""

    finding_sections: list[str] = []
    for i, fn in enumerate(findings, 1):
        p = fn["payload"]
        section = (
            f"Finding {i}: {p['title']}\n"
            f"  Application type: {p['application_type']}\n"
            f"  Severity: {p['severity']:.2f}\n"
            f"  Diagnosis:\n  {p['diagnosis_prompt']}"
        )
        finding_sections.append(section)

    evidence = "; ".join(event_summaries[:8]) if event_summaries else "none"
    recommendations = "; ".join(recommendation_texts) if recommendation_texts else "none"

    return (
        f"{source_line}"
        "Write a concise report summary (2-5 sentences) covering ALL findings below. "
        "Prioritize the highest-severity issues. "
        "Do not include numerical values, percentages, or event code numbers. "
        "Do not include file names or their .2020 extension. "
        "Do not use bullet points or JSON. Do not add new facts.\n\n"
        "Findings:\n"
        + "\n\n".join(finding_sections)
        + f"\n\nEvidence: {evidence}\n"
        f"Suggested checks: {recommendations}\n"
    )
```

**System message** (applied in each backend): `"You write concise agronomy incident summaries. Your tone should be suggestive."`

**Confidence calculation:**

```python
confidence_base = 0.62 if state["dataQualityFlag"] else 0.78
confidence = round(max(0.5, confidence_base - (0.06 if state["topSeverity"] < state["severityThreshold"] else 0)), 2)
```

| Condition | confidence_base | Final confidence |
|---|---|---|
| Full data, severity ≥ threshold | 0.78 | 0.78 |
| Full data, severity < threshold | 0.78 | 0.72 |
| Missing summaryData, severity ≥ threshold | 0.62 | 0.62 |
| Missing summaryData, severity < threshold | 0.62 | 0.56 |

> **Example: full LLM prompt for a real run**
> ```
> Source file: abc123.2020
> Write a concise report summary (2-5 sentences) covering ALL findings below. Prioritize the highest-severity issues. Do not include numerical values, percentages, or event code numbers. Do not include file names or their .2020 extension. Do not use bullet points or JSON. Do not add new facts.
>
> Findings:
> Finding 1: Seeder anomaly findings
>   Application type: seeding
>   Severity: 1.00
>   Diagnosis:
>   Seeder Findings:
> Anomalous metrics detected:
> Vacuum Right: +99900% / +0%, avg 0.001 (6 events)
> Vacuum Left: +25650% / +0%, avg 0.004 (6 events)
> Seed Spacing: +0% / -42%, avg 5.823 (4 events)
>
> Event codes:
> 10015: Seed Sensor Obstruction (6)
>     An obstruction has been detected on the seed sensor.
>     Recommendation: Inspect SpeedTube to look for a wedged seed or other obstruction near the seed sensor inside the belt housing.
>
> Evidence: Event code 10015 (Seed Sensor Obstruction) @ record 4821: 3 anomalous metrics. metrics.vacuum_right=10.023 (expected 0.001, +1002300%); ...; Event code 10015 @ record 5103: ...
> Suggested checks: none
> ```

---

### LLM backend dispatch

`generate_llm_summary()` in `llm_backends.py` dispatches to one of four backends. In `auto` mode it tries them in order, falling back on failure:

```python
async def generate_llm_summary(
    prompt: str, backend: str = "auto"
) -> tuple[str | None, str | None, str]:
    """Returns (summary, error, model_label)."""

    if backend == "local":
        llm_summary = await asyncio.to_thread(_local_summary, prompt)
        model_label = f"local/{os.getenv('LOCAL_MODEL_DIR') or os.getenv('LOCAL_MODEL_ID', 'unknown')}"

    elif backend == "vllm":
        llm_summary, llm_error = await asyncio.to_thread(_vllm_summary, prompt)
        model_label = f"vllm/{os.getenv('VLLM_MODEL', 'unknown')}"

    elif backend == "lambda":
        llm_summary = await asyncio.to_thread(_lambda_summary, prompt)
        model_label = f"lambda/{os.getenv('LAMBDA_MODEL', 'lambda_ai/llama3.1-8b-instruct')}"

    elif backend == "openai":
        llm_summary, model = await asyncio.to_thread(_openai_summary, prompt)
        model_label = f"openai/{model}"

    else:  # auto — try in order
        # local → vllm → lambda → openai
        ...

    return llm_summary, llm_error, model_label
```

**Fallback:** If all backends fail, `synthesize()` falls back to a structured text report built by concatenating the finding `diagnosis_prompts` with severity and confidence appended.

**Backend configuration:**

| Backend | Key env vars | Default model |
|---|---|---|
| Local HuggingFace | `LOCAL_MODEL_DIR` or `LOCAL_MODEL_ID`, `LOCAL_ADAPTER_DIR` (PEFT) | — |
| vLLM | `VLLM_BASE_URL`, `VLLM_MODEL` | — |
| Lambda.ai | `LAMBDA_API_KEY`, `LAMBDA_MODEL` | `lambda_ai/llama3.1-8b-instruct` |
| OpenAI | `OPENAI_API_KEY`, `OPENAI_MODEL` | `gpt-5.2` |

Sampling parameters: `LOCAL_MAX_NEW_TOKENS` (default `256`), `LOCAL_TEMPERATURE` (default `0.2`)


---

### Report node

The synthesized summary (or fallback text) is wrapped into a `Report` KG node:

```python
report_payload: ReportPayload = {
    "report_id":          report_id,
    "run_id":             state["runId"],
    "summary":            llm_summary or " ".join(report_lines),
    "severity":           state["topSeverity"],
    "confidence":         confidence,
    "finding_refs":       state["findingIds"],
    "priority_refs":      state["priorityIds"],
    "recommendation_refs": state["recommendationIds"],
}
```

`REPORT_SUMMARIZES` edges are added pointing to every Finding, Priority, and Recommendation node.

**Returns to state:** `reportId`, `llmModel`, `llmError`, `kg` (Report node)

---

## Knowledge Graph Schema

### NodeEnvelope

Every KG node uses the same container shape:

```python
class Edge(TypedDict):
    type: EdgeType
    to: str             # node_id of the target node

class NodeEnvelope(TypedDict):
    node_id:   str
    node_type: NodeType
    payload:   Any      # one of the Payload TypedDicts below
    edges:     list[Edge]
```

The full KG is `dict[node_id, NodeEnvelope]`, stored in `GraphState.kg`.

---

### Node types

```python
NodeType = Literal[
    "Run",            # root: one per pipeline invocation
    "Segment",        # reserved — not currently populated
    "WindowFeature",  # reserved — not currently populated
    "Event",          # one per anomalous event-code occurrence
    "Finding",        # aggregation of Events by application type
    "Priority",       # severity score attached to a Finding
    "Augmentation",   # RAG context (augment() node, not currently wired)
    "Recommendation", # suggested action (augment() node, not currently wired)
    "Report",         # final LLM-synthesized output
]
```

Nodes that are **currently populated** in a typical run: `Run`, `Event`, `Finding`, `Priority`, `Report`.

---

### Edge types

```python
EdgeType = Literal[
    "RUN_HAS_SEGMENT",              # reserved
    "SEGMENT_HAS_FEATURE",          # reserved
    "FEATURE_SUPPORTS_EVENT",       # reserved
    "EVENT_SUPPORTS_FINDING",       # Event → Finding (set in analyze_event_records)
    "FINDING_HAS_PRIORITY",         # Finding → Priority (set in prioritize)
    "FINDING_HAS_RECOMMENDATION",   # Finding → Recommendation (augment, not wired)
    "REPORT_SUMMARIZES",            # Report → Finding / Priority / Recommendation
    "NODE_HAS_TS_REF",              # reserved: time-series reference
    "NODE_HAS_SPATIAL_REF",         # reserved: GPS reference
]
```

---

## Key Data Models

### RunPayload

```python
class RunPayload(TypedDict):
    run_id:         str
    sample_rate_hz: int
    start_time_s:   float
    end_time_s:     float
    spatial_ref:    None
```

### EventPayload

```python
class EventPayload(TypedDict):
    event_id:        str
    run_id:          str
    event_code:      int          # integer event code from SystemLog.proto
    application_id:  str
    start_record:    int
    end_record:      int | None
    event_length:    int | None   # end_record - start_record
    severity:        float        # normalized 0.0 → 1.0
    priority_score:  float        # raw compute_priority() output
    anomalies: list[{
        "metric":    str,
        "summary":   float,       # run-level average from Tailor
        "record":    float,       # value at start_record from Stitch
        "pct_delta": float,       # (record - summary) / |summary|
        "abs_delta": float,
    }]
    summary:          str         # one-line human-readable description
    diagnosis_prompt: str         # structured string for LLM prompt
    evidence_refs:    list[str]   # ["record:{app_id}:{start_record}"]
```

### FindingPayload

```python
class FindingPayload(TypedDict):
    finding_id:       str
    run_id:           str
    application_id:   str
    application_type: str         # e.g. "seeding", "row_unit"
    application_name: str         # human-readable
    title:            str         # e.g. "Row Unit anomaly findings"
    severity:         float       # max severity across constituent Events
    event_refs:       list[str]   # event_ids of constituent Events
    metric_summaries: list[{
        "metric_key":    str,
        "metric_name":   str,
        "peak_pct_pos":  float,   # largest positive deviation seen
        "peak_pct_neg":  float,   # largest negative deviation seen
        "tailor_average": float,
        "event_count":   int,
    }]
    event_code_counts: dict[str, int]   # {"1001": 5, "1042": 2}
    diagnosis_prompt:  str              # proto-enriched, sent to LLM
```

### PriorityPayload

```python
class PriorityPayload(TypedDict):
    priority_id: str
    finding_id:  str
    score:       float    # same as Finding.severity, rounded to 2dp
    rationale:   str
```

### ReportPayload

```python
class ReportPayload(TypedDict):
    report_id:           str
    run_id:              str
    summary:             str       # LLM output (or structured fallback)
    severity:            float     # topSeverity from prioritize()
    confidence:          float     # 0.5 – 0.78, see confidence table above
    finding_refs:        list[str]
    priority_refs:       list[str]
    recommendation_refs: list[str]
```

---

## Configuration Reference

| Variable | Default | Used in | Description |
|---|---|---|---|
| `TAILOR_STREAM_URL` | — | `parse()` | Direct Tailor endpoint (overrides composed URL) |
| `TAILOR_BASE_URL` | — | `parse()` | Base URL for Tailor API |
| `TAILOR_ORG_CODE` | — | `parse()` | Organization code for URL composition |
| `TAILOR_STREAM_ID` | — | `parse()` | Stream ID for URL composition |
| `STITCH_LOCAL_BASE_URL` | `http://localhost:8888` | `analyze_event_records()` | Stitch file system endpoint |
| `EVENT_PROTO_PATH` | `SystemLog.proto` | `analyze_event_records()` | Path to protobuf event definitions |
| `COMPARE_PCT_THRESHOLD` | `0.25` | `compare_metrics()` | Minimum % deviation to flag anomaly |
| `COMPARE_ABS_THRESHOLD` | `0.0` | `compare_metrics()` | Minimum absolute deviation (AND condition) |
| `COMPARE_MAX_EVENTS` | `20` | `analyze_event_records()` | Max events to process (top by priority) |
| `OPENAI_API_KEY` | — | `llm_backends.py` | OpenAI authentication |
| `OPENAI_MODEL` | `gpt-5.2` | `llm_backends.py` | OpenAI model ID |
| `LOCAL_MODEL_DIR` | — | `llm_backends.py` | Local HuggingFace model directory |
| `LOCAL_MODEL_ID` | — | `llm_backends.py` | HuggingFace Hub model ID |
| `LOCAL_ADAPTER_DIR` | — | `llm_backends.py` | PEFT/LoRA adapter directory |
| `LOCAL_BASE_MODEL` | — | `llm_backends.py` | Base model for adapter loading |
| `LOCAL_MAX_NEW_TOKENS` | `256` | `llm_backends.py` | Token limit for local inference |
| `LOCAL_TEMPERATURE` | `0.2` | `llm_backends.py` | Sampling temperature for local inference |
| `LAMBDA_API_KEY` | — | `llm_backends.py` | Lambda.ai API key |
| `LAMBDA_MODEL` | `lambda_ai/llama3.1-8b-instruct` | `llm_backends.py` | Lambda.ai model ID |
| `VLLM_BASE_URL` | — | `llm_backends.py` | vLLM server URL |
| `VLLM_MODEL` | — | `llm_backends.py` | vLLM model name |

---

## Architecture Notes

### Merge-reducer state accumulation

The KG is never built in one place. Each node returns only the nodes it creates, and LangGraph merges them via `merge_dict`. This means nodes are fully independent and can be tested in isolation by passing a partial `GraphState`.

### Async-first with thread offloading

All four pipeline nodes are `async def`. Blocking calls — Stitch HTTP requests, local HuggingFace inference — are wrapped in `asyncio.to_thread()` so they don't block the event loop. The pipeline runs with `graph.ainvoke(state)`.

### Proto enrichment as a prompt engineering technique

Rather than sending raw metric keys and integer event codes to the LLM, the pipeline resolves them against `SystemLog.proto` at Finding construction time. The resulting `diagnosis_prompt` already contains human-readable code titles, descriptions, and agronomic recommendations, giving the LLM the domain context it needs without requiring a fine-tuned model.

### Two-level anomaly aggregation

- **Event level** — per-occurrence: was *this specific record* anomalous? Captures exact location, span, and per-metric deviations.
- **Finding level** — per-application-type: what is the overall pattern for *this subsystem* across the entire run? Aggregates peak deviations, code frequencies, and event counts into a single diagnostic picture.

### The augment() node (not currently wired)

`augment()` (graph.py:604) would insert a RAG step between `prioritize()` and `synthesize()` for high-severity runs. It creates `Augmentation` nodes (retrieval snippets) and `Recommendation` nodes (suggested actions), which are then available to the LLM via `recommendation_texts` in the prompt. The routing condition is `topSeverity >= severityThreshold`. Currently, `add_edge("prioritize", "synthesize")` bypasses it entirely.

### DemoController

`with_demo(name, fn)` wraps each node. When `state["demo"]` is `True` and a `demoController` is attached, the wrapper pauses after the node completes so a UI layer can stream intermediate KG state to the frontend. In all other cases it is a zero-overhead passthrough.
