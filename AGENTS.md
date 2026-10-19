# AGENTS.md — Catalysk Event Timeline Reconstruction Assessment

This file is the operating contract for coding agents working in this repository. It is specific to the Catalysk internship assessment. Read it before changing anything.

## 1. Project objective and boundaries

Build a Python 3.10+ command-line engine that:

- Loads e-commerce order-event CSV data.
- Normalizes and validates records according to the supplied rules.
- Identifies duplicate and conflicting event IDs.
- Reconstructs each order's timeline from `event_time`.
- Applies the supplied business state machine.
- Writes a per-order summary and an anomaly report.

Optimize for **correctness, deterministic behavior, robustness, testing, and explainability**. Do not optimize for dashboards, visual polish, ML, microservices, or unnecessary architecture.

Hard boundaries:

- No ML model, external API, paid service, private data, credentials, or network access is required or permitted for the assignment implementation.
- Use Python 3.10+ and prefer the standard library. Add a free open-source dependency only if it provides clear value; if used, declare it in `requirements.txt` and explain why in the final project README.
- Do not fabricate results. Never claim tests passed, counts were produced, the full dataset ran, or validation occurred unless the actual command output is available as evidence.

## 2. Verified repository layout

Verified in the current workspace:

```text
Catalysk/
  AGENTS.md                       # this file
  README.md                       # supplied assignment specification
  highlights.json                 # unexplained empty artifact
  data/
    DATA_DICTIONARY.md            # supplied input contract
    order_events.csv              # full main dataset
    debug_subset.csv              # smaller smoke-test subset
    worked_example_input.csv      # fixture input
    worked_example_summary.csv    # expected fixture summary
    worked_example_anomalies.csv  # expected fixture anomalies
    highlights.json               # unexplained empty artifact
```

Observed evidence:

| File | Observed size | Observed raw line count |
|---|---:|---:|
| `README.md` | 9,490 bytes | 167 |
| `data/DATA_DICTIONARY.md` | 2,540 bytes | 28 |
| `data/order_events.csv` | 21,547,624 bytes | 181,776 including header |
| `data/debug_subset.csv` | 860,528 bytes | 7,265 including header |
| `data/worked_example_input.csv` | 1,541 bytes | 18 including header |
| `data/worked_example_summary.csv` | 388 bytes | 8 including header |
| `data/worked_example_anomalies.csv` | 630 bytes | 10 including header |
| `highlights.json` (root and `data/`) | 17 bytes each | content is `{"files": {}}` |

These are raw line counts, not verified parsed record counts. Do not claim an exact number of parsed records or orders until a run produces and verifies it.

There is no `main.py`, test suite, `requirements.txt`, or `output/` directory yet. Do not pretend otherwise.

The workspace is not currently a Git repository. Do not initialize one or run Git-changing commands without explicit user authorization.

## 3. Immutable supplied material

Treat all supplied assessment files as read-only:

- `README.md` (assignment specification)
- `data/DATA_DICTIONARY.md`
- `data/order_events.csv`
- `data/debug_subset.csv`
- `data/worked_example_input.csv`
- `data/worked_example_summary.csv`
- `data/worked_example_anomalies.csv`
- `highlights.json`
- `data/highlights.json`

Do not modify, delete, move, rename, reformat, regenerate, or "clean" any of them. Do not add the large `data/order_events.csv` to a submission or commit unless the user explicitly requests it.

The two `highlights.json` files are not referenced by the assignment specification or data dictionary. Treat them as unrelated environment artifacts: do not use them in logic, do not parse them for requirements, and do not modify or delete them.

## 4. Source-of-truth hierarchy

When instructions conflict, use this order:

1. The supplied assignment specification (`README.md`).
2. `data/DATA_DICTIONARY.md`, which clarifies strict input conventions.
3. Explicit user instructions for the current authorized stage.
4. The worked-example fixtures, which are validation oracles only.

Rules:

- The specification and data dictionary govern exact behavior. Do not silently relax, reinterpret, or "improve" them.
- The worked example and its expected CSVs validate behavior; they are **not** a lookup table. Never hardcode fixture IDs, counts, rows, or answers into production logic.
- A user instruction may authorize a specific stage or workflow, but it does not silently override an explicit assessment rule. If it conflicts, stop and report the conflict before proceeding.
- Keep explicit requirements, recommendations, and unresolved assumptions visibly separate.
- Do not infer domain meaning from identifier prefixes, numeric-looking ID portions, row positions, `received_at`, `source_system`, or `metadata`.

## 5. Staged workflow and evidence discipline

Follow the user's staged prompt/review process. Do only the task authorized by the current stage.

- **PLAN mode:** inspect and propose. Do not edit implementation files unless the prompt explicitly authorizes a specific planning artifact.
- **BUILD mode:** implement only the approved scope, then run only the checks permitted by the prompt.
- If requirements conflict, a required file is missing, or a material ambiguity blocks correct implementation, stop and report it instead of guessing.
- Do not skip ahead to later stages (for example, do not write tests or run the full dataset during a planning-only stage).

At the end of every stage, report:

1. Files changed.
2. Decisions made.
3. Commands actually run and their results.
4. Test/validation outcomes with evidence.
5. Remaining risks, unverified claims, and deviations.
6. Anything awaiting user confirmation.

Never use inspection alone as evidence that tests pass or that output is correct.

## 6. Input schema and normalization

Required input columns, by name:

```text
row_id,event_id,order_id,event_type,event_time,received_at,source_system,metadata
```

Rules:

- Verify required columns exist before processing. If the schema is unusable, fail with a clear error rather than inventing data.
- Trim surrounding whitespace from every input value. Empty and whitespace-only cells are missing.
- Normalize `event_type` by trimming and uppercasing **only**. Do not invent aliases, split labels, remove or alter underscores, or map similar names.
- `event_id` and `order_id` are case-sensitive opaque strings after trimming.
- `row_id` is a unique, nonmissing, deterministic ingestion-row identifier. It is not a timestamp and must not be parsed as one.
- A missing `event_id` must never be grouped with another missing `event_id`.
- `received_at`, `source_system`, and `metadata` are informational only. They must not determine event identity, canonical selection, chronological order, validation labels, or transitions.
- `metadata` must not be parsed for behavior unless the user receives an explicit new requirement; the supplied dictionary says it is not used by the required engine.

If an input violates the stated `row_id` uniqueness/nonmissing invariant, do not invent a new anomaly label. Surface it as a clear malformed-input error.

## 7. Canonical identity and deduplication

Perform duplicate/conflict detection **before** single-row validation and before timeline processing.

For each repeated **nonempty** `event_id`:

1. Choose the canonical row as the one with the lexicographically smallest trimmed `row_id`. This must be independent of CSV row order.
2. For every other row sharing that `event_id`, build the identity tuple:

   ```text
   (trimmed order_id, normalized event_type, trimmed raw event_time)
   ```

3. Compare the noncanonical row's tuple with the canonical row's tuple:
   - Equal tuple -> `DUPLICATE_EVENT`.
   - Different tuple -> `EVENT_ID_CONFLICT`.
4. Reject every noncanonical row immediately. It must not enter validation or the state machine.

Important:

- Identity is **not** "the whole CSV row is identical". It is the specified tuple above.
- The comparison uses the raw trimmed `event_time` string, not a parsed timestamp. Two rows with the same malformed timestamp string can therefore be duplicates; differing malformed strings are conflicts.
- Rows with empty `event_id` are not grouped at all.
- A conflict-only order still receives an order summary; see section 11.

## 8. Strict timestamp handling

Only these three full-second UTC representations are valid:

```text
YYYY-MM-DDTHH:MM:SSZ
YYYY-MM-DD HH:MM:SS
YYYY-MM-DDTHH:MM:SS+00:00
```

Validation requirements:

- Validate the exact shape with a full-string regex **and** validate real calendar/time validity (for example with `datetime.strptime`). A permissive parser alone is insufficient because it may accept non-zero-padded or otherwise unsupported forms.
- Reject impossible dates/times, date-only values, timestamps missing seconds, fractional seconds, nonzero timezone offsets, lowercase `z`, and any format outside the three above, even if a permissive parser would accept it.
- Treat the two unzoned forms as UTC. The `Z` and `+00:00` forms are also UTC.
- Normalize accepted values to timezone-aware UTC datetimes for ordering.
- Emit all accepted timestamps as `YYYY-MM-DDTHH:MM:SSZ`.
- Never use `received_at` as a fallback or as a substitute ordering key.
- Do not accept leap seconds, `24:00:00`, or other values that are not real times.

The data dictionary's strict-format rule governs. The specification's "seconds precision is sufficient" statement means exactly full seconds; it does not authorize fractional seconds.

## 9. Validation precedence

For canonical rows (and ungrouped rows with empty `event_id`), emit **only the first applicable error** in exactly this order:

1. Empty `order_id` -> `MISSING_ORDER_ID`.
2. Empty `event_id` -> `MISSING_EVENT_ID`.
3. Empty `event_time` -> `MISSING_TIMESTAMP`.
4. Unparseable or unsupported `event_time` -> `INVALID_TIMESTAMP`.
5. `event_type` not in the required vocabulary -> `UNKNOWN_EVENT`.
6. Otherwise, the row is eligible for timeline processing.

Consequences:

- No row may receive more than one anomaly.
- Duplicate/conflict rejection happens before this sequence.
- An empty `event_type` is not a separate "missing field" category; it fails step 5 as `UNKNOWN_EVENT`.
- Never create anomaly labels other than the eight allowed labels listed in section 11.

Required event vocabulary:

```text
PLACED, PAID, PACKED, SHIPPED, OUT_FOR_DELIVERY, DELIVERED,
CANCELLED, RETURN_REQUESTED, RETURNED, REFUNDED
```

`START` is the initial state, not an input event type.

## 10. Timeline ordering and state machine

Process only eligible rows.

- Group eligible rows by trimmed `order_id`.
- Sort each order's events ascending by:

  ```text
  (parsed event_time, trimmed event_id, trimmed row_id)
  ```

- Never use CSV row order or `received_at` for chronology.

Allowed transitions (exactly):

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
| `CANCELLED` | none |
| `REFUNDED` | none |

Rules:

- `CANCELLED` and `REFUNDED` are terminal.
- Same-state transitions are invalid; none are listed.
- On an invalid transition:
  - Emit `INVALID_TRANSITION`.
  - Do not change the current state.
  - Continue processing later events using the last accepted state.
- Do not synthesize missing events, reorder history by inference, or repair timelines.
- A valid prefix remains valid even if later events are invalid.
- Count only accepted transitions in `valid_events`. `PLACED` counts as one accepted transition.
- `first_valid_event_time` and `last_valid_event_time` come only from accepted transitions, not from rejected ones.

## 11. CLI and output contract

Required command:

```bash
python main.py --input PATH --output-dir DIR
```

Both options are required. The program must accept arbitrary input paths conforming to the schema without code changes, retraining, or external services.

Create the output directory if needed, then write:

- `<output-dir>/order_summary.csv`
- `<output-dir>/anomalies.csv`

### `order_summary.csv`

Exact column order:

```csv
order_id,final_status,valid_events,anomaly_count,first_valid_event_time,last_valid_event_time
```

Rules:

- Include one row for every distinct nonempty `order_id` appearing **anywhere** in the input, including rejected rows, duplicate/conflict-only orders, and rows that never reached the state machine.
- `final_status` is the last accepted state, or `UNKNOWN` if no event was accepted.
- `valid_events` is the count of accepted transitions (including `PLACED`).
- `anomaly_count` is the number of rows in `anomalies.csv` attributed to that order.
- `first_valid_event_time` / `last_valid_event_time` are UTC `YYYY-MM-DDTHH:MM:SSZ`, or empty when no event was accepted.
- Integer fields must be written as integers, not floats or formatted strings.
- Sort rows by `order_id` ascending.
- Every order with no accepted event uses `UNKNOWN`, `0`, ``, ``.

### `anomalies.csv`

Exact column order:

```csv
row_id,order_id,event_id,anomaly_type,details
```

Rules:

- Include one row for each rejected input row, and at most one anomaly per `row_id`.
- Use trimmed `row_id`, `order_id`, and `event_id` values. Empty values remain empty.
- `details` is clear human-readable text. It is the only field whose exact wording is not compared by the fixture validation; still keep it deterministic and free of volatile data.
- Sort rows by `row_id` ascending (lexicographic string order).
- Allowed `anomaly_type` values, exactly:

```text
DUPLICATE_EVENT
EVENT_ID_CONFLICT
MISSING_ORDER_ID
MISSING_EVENT_ID
MISSING_TIMESTAMP
INVALID_TIMESTAMP
UNKNOWN_EVENT
INVALID_TRANSITION
```

### Anomaly accounting

- A rejected row with a nonempty trimmed `order_id` contributes to that order's `anomaly_count`, even if it never entered the state machine.
- A row with an empty `order_id` appears in `anomalies.csv` but contributes to no order summary count.
- Duplicate/conflict rejection, single-row validation, and transition failures all count as anomalies for their respective nonempty orders.
- `anomaly_count` must reconcile with the number of anomaly rows for that order.
- Write both files even when one or both are empty apart from headers.
- Use Python's `csv` module with proper quoting, `newline=""`, and UTF-8 output. Do not hand-roll CSV escaping or concatenate lines manually.

## 12. Determinism, performance, and robustness

Determinism:

- Output must be invariant under input-row permutation, including canonical duplicate selection and timeline tie-breaking.
- Do not rely on dict/set iteration order as an output guarantee. Sort explicitly where the contract requires order.
- Reruns on the same input must produce sematically identical output.

Performance:

- The full dataset is approximately 181,000 rows and 30,000 orders. It must finish on a typical laptop without manual dataset modification.
- Use a straightforward algorithm: one pass to normalize/group, `O(N)` canonical selection per `event_id`, then per-order sorting. Total time is `O(N log N)` in the worst case; memory is `O(N)`.
- Do not repeatedly scan the entire dataset for each order.
- Avoid premature optimization, but also avoid building unnecessary in-memory copies of the whole dataset.

Robustness:

- Handle missing/unreadable input files, malformed CSV, missing required columns, invalid output paths, and filesystem failures with clear error messages and a nonzero exit status.
- Do not fabricate defaults or silently drop data.
- Never load the full dataset into an agent conversation. Inspect with scripts/tools and summarize counts/statistics instead.

## 13. Testing and validation protocol

Use Python `unittest` unless an existing test framework is already present. Do not add third-party test dependencies without a reason.

At minimum, include five meaningful unit tests covering:

1. Duplicate events and conflicting event IDs, including canonical selection by smallest trimmed `row_id`.
2. Chronological ordering by `event_time`, with `event_id` and `row_id` tie-breaking, ignoring CSV row order and `received_at`.
3. Invalid transitions: anomaly emitted, state unchanged, later valid events still processed.
4. Missing/unknown fields and exact validation precedence (only the first applicable anomaly).
5. Strict timestamp acceptance/rejection for all three allowed forms and the forbidden variants.
6. Summary/anomaly accounting, including conflict-only orders and rows with empty `order_id`.
7. Determinism/reproducibility after shuffling input rows.

Fixture validation:

- Run the engine on `data/worked_example_input.csv`.
- Compare semantically against `data/worked_example_summary.csv` and `data/worked_example_anomalies.csv`.
- Ignore only the free-text `details` descriptions, as permitted by the specification.
- Do not hardcode fixture-specific answers into production logic.

Validation sequence:

1. Run unit tests and record the actual command and result.
2. Smoke-test on `data/debug_subset.csv` where useful.
3. Run on the complete `data/order_events.csv`.
4. Verify both output files exist and have exact headers.
5. Verify order counts, anomaly-type counts, one anomaly per rejected `row_id`, and `anomaly_count` reconciliation.
6. Verify accepted-event timestamps and final statuses are consistent with the state machine.
7. Shuffle the input rows and verify semantic reproducibility.

Never claim a test or run passed without its command/result as evidence.

## 14. Required submission package

The final package must contain:

- `main.py` and any necessary supporting Python modules.
- `requirements.txt` only if external packages are used.
- A final project `README.md` covering setup, command usage, design decisions, complexity, limitations, and significant AI tools/packages used.
- At least five meaningful unit tests.
- `output/order_summary.csv` and `output/anomalies.csv` generated from the complete main dataset.
- A maximum one-page analysis note with total orders, event and anomaly counts, 2-3 real observed edge cases, and one engineering trade-off.

Do not include the large original input CSV unless the user explicitly requests it.

**README collision warning:** the supplied `README.md` at the repository root is the assignment specification, not the final project README. The submission also requires a final `README.md`. Do not overwrite, rename, or modify the supplied specification. Ask the user how to handle this naming collision before creating the final project README. This is an unresolved issue.

## 15. Coding style and change discipline

Style:

- Clear, idiomatic Python.
- Small functions with meaningful names and focused responsibilities.
- Useful type hints where they improve clarity.
- Comments should explain reasoning or non-obvious rules, not restate code.
- Prefer `argparse`, `csv`, and `datetime` from the standard library.
- Avoid speculative frameworks, excessive abstraction, global mutable state, clever one-liners, unnecessary configuration, and unrelated features.

Change discipline:

- Inspect existing files and diffs before modifying them.
- Keep changes scoped to the current authorized task.
- Do not silently overwrite user data or delete files.
- Do not create files outside the authorized scope.
- Do not commit, push, create branches, change remotes, or perform any Git-changing operation unless the user explicitly authorizes that stage.
- Never commit supplied datasets, generated outputs from private data, credentials, or secrets unless specifically instructed.

## 16. Definition of done for an implementation stage

A stage is complete only when:

- The authorized scope is implemented.
- Tests required by that stage have actually been run, with results shown.
- Fixture comparisons and any required dataset runs have actually been performed, with evidence.
- Output headers, counts, and accounting reconciliation have been checked.
- Determinism after row shuffling has been checked when required.
- The final report includes files changed, decisions, commands run, outcomes, risks, and deviations.

## 17. Unresolved issues and source discrepancies

Record these honestly; do not invent resolutions:

1. **Missing PDF:** `Event_Timeline_Assessment.pdf` was reported by the user but is not present on disk. Its contents could not be read or compared with `README.md`. Do not claim PDF requirements were verified.
2. **Missing `README(7).md`:** the reported filename does not exist. The actual specification is `README.md` at the repository root.
3. **README naming collision:** the supplied `README.md` is the assignment specification, while the submission also requires a final `README.md`. The user must decide how to distinguish or place the final README before any change to the supplied file.
4. **Unexplained `highlights.json` files:** present at the root and in `data/`, each 17 bytes with content `{"files": {}}`, not referenced by the assignment or data dictionary. Leave them untouched and do not use them in implementation logic.
5. **Unverified counts:** observed raw line counts are not parsed record counts. The approximate "181,000 rows / 30,000 orders" figures have not been independently verified by processing the dataset.
6. **PDF/Markdown consistency:** unverified because the PDF is absent.
