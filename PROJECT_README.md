# Event Timeline Reconstruction Engine

A command-line Python tool that reconstructs e-commerce order timelines from a
messy event-log CSV. It normalizes and validates records, detects duplicated and
conflicting event IDs, replays each order through a fixed business state machine,
and writes a per-order summary plus an anomaly report.

> **Note on naming:** the root [`README.md`](README.md) is the original Catalysk
> assignment specification. This file (`PROJECT_README.md`) documents the
> implementation.

## Features

- **Input normalization** — trims every cell; whitespace-only cells are treated
  as missing; `event_type` is trimmed and uppercased only (no aliasing).
- **Duplicate / conflict handling** — repeated nonempty `event_id` values are
  grouped before validation. The canonical row is the one with the
  lexicographically smallest trimmed `row_id` (independent of CSV order). Each
  noncanonical row is rejected as `DUPLICATE_EVENT` when its ordered
  `(order_id, event_type, raw event_time)` identity matches the canonical row, or
  `EVENT_ID_CONFLICT` when it differs. Rows with empty `event_id` are never
  grouped.
- **Single-row validation** — exactly the first applicable check is reported, in
  this precedence: `MISSING_ORDER_ID`, `MISSING_EVENT_ID`, `MISSING_TIMESTAMP`,
  `INVALID_TIMESTAMP`, `UNKNOWN_EVENT`.
- **Chronological reconstruction** — eligible events are sorted by
  `(parsed event_time, event_id, row_id)`; CSV position and `received_at` are
  never used for ordering.
- **State machine** — allowed transitions are applied sequentially from `START`;
  invalid transitions are reported without changing the current state, and later
  events continue to be evaluated.
- **Deterministic output** — summaries are sorted by `order_id`, anomalies by
  `row_id`; repeated runs and shuffled input produce identical files.
- **Reporting** — one summary row per distinct nonempty `order_id` anywhere in the
  input, and one anomaly row per rejected input row.

## Requirements

- Python **3.10 or newer** (the code uses `from __future__ import annotations`,
  PEP 604 `X | None` hints, and builtin generics).
- **No third-party dependencies.** The implementation uses only the standard
  library (`argparse`, `csv`, `datetime`, `pathlib`, `collections`, `re`), so no
  `requirements.txt` is needed.

## Repository layout

```text
README.md                         supplied assignment specification (unchanged)
PROJECT_README.md                 this project guide
AGENTS.md                         working contract for coding agents
engine.py                         core pipeline and state machine
main.py                           CLI entry point
tests/test_engine.py              engine/pipeline unit tests
tests/test_cli.py                 subprocess CLI tests
data/DATA_DICTIONARY.md           supplied input contract
data/order_events.csv             full dataset (present locally; git-ignored)
data/debug_subset.csv             smaller dataset for smoke tests
data/worked_example_input.csv     hand-checkable fixture input
data/worked_example_summary.csv   expected fixture summary
data/worked_example_anomalies.csv expected fixture anomalies
output/order_summary.csv          generated per-order summary
output/anomalies.csv              generated anomaly report
docs/ANALYSIS.md                  one-page analysis of the full-dataset run
```

`data/order_events.csv` is intentionally excluded by `.gitignore` because of its
size.

## Setup

No installation step is required. From the repository root:

```bash
python --version        # confirm 3.10+
```

## Usage

```bash
python main.py --input PATH --output-dir DIR
```

- `--input` — path to an input CSV with the required columns
  `row_id,event_id,order_id,event_type,event_time,received_at,source_system,metadata`.
- `--output-dir` — directory for the two output files; created if it does not
  exist.

Both options are required. Full-dataset example:

```bash
python main.py --input data/order_events.csv --output-dir output
```

Smaller smoke test:

```bash
python main.py --input data/debug_subset.csv --output-dir debug_output
```

Exit status is `0` on success. Input, output, or integrity failures print a
message to stderr and return `1`; missing command-line arguments return `2` via
`argparse`.

## Output schemas

`order_summary.csv`:

```csv
order_id,final_status,valid_events,anomaly_count,first_valid_event_time,last_valid_event_time
```

`anomalies.csv`:

```csv
row_id,order_id,event_id,anomaly_type,details
```

Both files are always written, even when there are no data rows. CSV output is
UTF-8 and produced with Python's `csv` module.

## Behavior and semantics

### Timestamps

Only three full-second, ASCII-digit representations are accepted:

- `YYYY-MM-DDTHH:MM:SSZ`
- `YYYY-MM-DD HH:MM:SS`
- `YYYY-MM-DDTHH:MM:SS+00:00`

Unzoned values are treated as UTC. Each value must match the exact shape **and**
represent a real calendar time; impossible dates, `24:00:00`, leap seconds,
fractional seconds, nonzero offsets, lowercase `z`, and non-ASCII digits are
rejected. Accepted values are emitted as `YYYY-MM-DDTHH:MM:SSZ` in UTC.

### Validation precedence

A canonical row receives at most one validation anomaly, chosen in this order:

1. Empty `order_id` → `MISSING_ORDER_ID`
2. Empty `event_id` → `MISSING_EVENT_ID`
3. Empty `event_time` → `MISSING_TIMESTAMP`
4. Unsupported/unparseable `event_time` → `INVALID_TIMESTAMP`
5. `event_type` outside the required vocabulary (including empty) → `UNKNOWN_EVENT`

### State machine

| Current state | Allowed next event |
|---|---|
| `START` | `PLACED` |
| `PLACED` | `PAID`, `CANCELLED` |
| `PAID` | `PACKED`, `CANCELLED` |
| `PACKED` | `SHIPPED`, `CANCELLED` |
| `SHIPPED` | `OUT_FOR_DELIVERY` |
| `OUT_FOR_DELIVERY` | `DELIVERED` |
| `DELIVERED` | `RETURN_REQUESTED` |
| `RETURN_REQUESTED` | `RETURNED` |
| `RETURNED` | `REFUNDED` |
| `CANCELLED` | none (terminal) |
| `REFUNDED` | none (terminal) |

Invalid transitions emit `INVALID_TRANSITION`, leave the state unchanged, and do
not stop later events. Only accepted transitions increment `valid_events`
(`PLACED` counts as one).

### Summaries and anomalies

- Every distinct nonempty `order_id` appearing anywhere in the input gets exactly
  one summary row — including orders that appear only in rejected rows.
- If no event was accepted, the row is `UNKNOWN, 0, "", ""`.
- `anomaly_count` equals the number of anomaly rows for that order; anomalies
  with an empty `order_id` appear in `anomalies.csv` but contribute to no
  summary.
- Anomaly labels are limited to: `DUPLICATE_EVENT`, `EVENT_ID_CONFLICT`,
  `MISSING_ORDER_ID`, `MISSING_EVENT_ID`, `MISSING_TIMESTAMP`,
  `INVALID_TIMESTAMP`, `UNKNOWN_EVENT`, `INVALID_TRANSITION`.
- `received_at`, `source_system`, and `metadata` are read but never used for
  identity, ordering, validation, or transitions.

### Input integrity

`row_id` must be unique and nonempty. A blank or duplicated `row_id` is treated
as malformed input: the run aborts with a clear error and exit status `1` rather
than inventing an anomaly label. Missing required columns, empty files, malformed
CSV, and non-UTF-8 input are handled the same way.

## Testing

Run the standard-library suite from the repository root:

```bash
python -m unittest discover -s tests -v
```

The suite contains 34 tests covering canonical duplicate/conflict selection,
empty `event_id` grouping, validation precedence, the three timestamp forms and
their rejected variants, ordering and tie-breaks, invalid transitions, the full
lifecycle and terminal states, summary/anomaly accounting, CLI behavior
(including malformed CSV and invalid UTF-8), row-ID integrity, permutation
reproducibility, and an exact comparison against the supplied worked-example
summary and anomaly fixtures (ignoring only the free-text `details` field).

## Performance

In one observed local run (Python 3.12.6 on Windows), the full dataset of
**181,775 records** completed in **approximately 3.0 seconds**, and a shuffled
rerun took approximately 3.1 seconds. This is a single local observation, not a
benchmark or a performance guarantee. The pipeline is `O(N log N)` in the worst
case (per-order sorting) with `O(N)` memory, because all normalized rows are held
in memory.

## Limitations and tradeoffs

- **Strict timestamps.** Ambiguous or nonstandard representations are flagged
  (`INVALID_TIMESTAMP`) rather than coerced. This avoids silently reordering a
  timeline, at the cost of rejecting values a permissive parser might accept.
- **Duplicate/conflict detection runs before validation.** A replayed row is
  classified by event identity even if its timestamp is malformed; this keeps one
  anomaly per row and avoids double-counting.
- **Whole-input memory use.** The engine loads all records and builds per-order
  event lists in memory; it does not stream or use a database.
- **Hard failure on `row_id` violations.** The assessment assumes unique,
  nonmissing `row_id`; the engine enforces that invariant instead of extending
  the anomaly vocabulary.
- **Informational columns ignored.** `received_at`, `source_system`, and
  `metadata` cannot influence results.
- **Anomaly `details`** are deterministic, human-readable strings; only the
  stable fields (`row_id`, labels, normalized identities) should be relied on by
  downstream automation.
