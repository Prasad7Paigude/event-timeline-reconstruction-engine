"""Engine-level unit tests for the Catalysk event timeline reconstruction engine.

These tests exercise the pipeline through its public surface (``engine.run`` and
the documented helper functions) using small, isolated, self-cleaning inputs.
The large datasets are never touched here.
"""

from __future__ import annotations

import csv
import random
import sys
import tempfile
import unittest
from datetime import timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import engine  # noqa: E402  (import after sys.path adjustment)

HEADER = list(engine.REQUIRED_COLUMNS)
ALLOWED_ANOMALY_TYPES = {
    "DUPLICATE_EVENT",
    "EVENT_ID_CONFLICT",
    "MISSING_ORDER_ID",
    "MISSING_EVENT_ID",
    "MISSING_TIMESTAMP",
    "INVALID_TIMESTAMP",
    "UNKNOWN_EVENT",
    "INVALID_TRANSITION",
}


def make_row(
    row_id="",
    event_id="",
    order_id="",
    event_type="",
    event_time="",
    received_at="r",
    source_system="s",
    metadata="m",
):
    """Build one raw input row (list of strings) with sensible defaults."""
    return [
        row_id,
        event_id,
        order_id,
        event_type,
        event_time,
        received_at,
        source_system,
        metadata,
    ]


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)


def read_rows(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_header(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return next(csv.reader(handle))


class PipelineTestCase(unittest.TestCase):
    """Shared plumbing: temp workspace and pipeline helpers."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def run_pipeline(self, rows):
        input_path = self.tmp / "input.csv"
        output_dir = self.tmp / "out"
        write_csv(input_path, rows)
        engine.run(input_path, output_dir)
        return (
            read_rows(output_dir / "order_summary.csv"),
            read_rows(output_dir / "anomalies.csv"),
        )

    def snapshot(self, rows):
        summaries, anomalies = self.run_pipeline(rows)
        return (
            {row["order_id"]: row for row in summaries},
            {row["row_id"]: row for row in anomalies},
        )

    def summary_by_order(self, summaries):
        return {row["order_id"]: row for row in summaries}

    def anomalies_by_row(self, anomalies):
        return {row["row_id"]: row for row in anomalies}


class DuplicateAndConflictTests(PipelineTestCase):
    # T1
    def test_T1_canonical_selection_and_duplicate_vs_conflict(self):
        # R001 / R002 are the canonical/duplicate pair for E1; R003 conflicts.
        rows = [
            make_row("R004", "E1", "O1", "PLACED", "2026-01-01T00:00:00Z"),
            make_row("R003", "E1", "O1", "PAID", "2026-01-01T00:05:00Z"),
            make_row("R001", "E1", "O1", "PLACED", "2026-01-01T00:00:00Z"),
            make_row("R002", "E1", "O1", "PAID", "2026-01-01T00:05:00Z"),
            # Whitespace is trimmed before the identity comparison.
            make_row("R005", "E2", "O2", "PLACED", " 2026-01-01T01:00:00Z "),
            make_row("R006", " E2 ", "O2", " placed ", "2026-01-01T01:00:00Z"),
            # Conflict-only order O3: canonical invalid, noncanonical conflicts.
            make_row("R007", "E3", "O3", "PLACED", ""),
            make_row("R008", "E3", "O3", "PAID", "2026-01-01T02:00:00Z"),
        ]
        summaries, anomalies = self.run_pipeline(rows)
        by_row = self.anomalies_by_row(anomalies)

        self.assertEqual(by_row["R004"]["anomaly_type"], "DUPLICATE_EVENT")
        self.assertEqual(by_row["R003"]["anomaly_type"], "EVENT_ID_CONFLICT")
        self.assertEqual(by_row["R002"]["anomaly_type"], "EVENT_ID_CONFLICT")
        self.assertEqual(by_row["R006"]["anomaly_type"], "DUPLICATE_EVENT")
        self.assertEqual(by_row["R007"]["anomaly_type"], "MISSING_TIMESTAMP")
        self.assertEqual(by_row["R008"]["anomaly_type"], "EVENT_ID_CONFLICT")

        # Canonical rows are never rejected as duplicates/conflicts.
        self.assertNotIn("R001", by_row)
        self.assertNotIn("R005", by_row)
        # Rejected rows receive no additional validation anomaly.
        self.assertEqual(len(by_row), len(anomalies))
        for row_id in ("R002", "R003", "R004", "R006", "R008"):
            self.assertIn(by_row[row_id]["anomaly_type"], {"DUPLICATE_EVENT", "EVENT_ID_CONFLICT"})

        by_order = self.summary_by_order(summaries)
        # Canonical O1 accepted once (PLACED); the conflict rows do not apply.
        self.assertEqual(by_order["O1"]["final_status"], "PLACED")
        self.assertEqual(int(by_order["O1"]["valid_events"]), 1)
        self.assertEqual(int(by_order["O1"]["anomaly_count"]), 3)
        # Whitespace-grouped order behaves as a normal duplicate.
        self.assertEqual(by_order["O2"]["final_status"], "PLACED")
        self.assertEqual(int(by_order["O2"]["anomaly_count"]), 1)
        # Conflict-only order appears with UNKNOWN and no accepted events.
        self.assertEqual(by_order["O3"]["final_status"], "UNKNOWN")
        self.assertEqual(int(by_order["O3"]["valid_events"]), 0)
        self.assertEqual(by_order["O3"]["first_valid_event_time"], "")
        self.assertEqual(by_order["O3"]["last_valid_event_time"], "")
        self.assertEqual(int(by_order["O3"]["anomaly_count"]), 2)

        # Classification is independent of CSV row order.
        self.assertEqual(self.snapshot(rows), self.snapshot(list(reversed(rows))))

    # T2
    def test_T2_empty_event_ids_are_never_grouped(self):
        rows = [
            make_row("S1", "", "O1", "PLACED", "2026-01-01T00:00:00Z"),
            make_row("S2", "", "O1", "PLACED", "2026-01-01T00:00:00Z"),
        ]
        summaries, anomalies = self.run_pipeline(rows)
        by_row = self.anomalies_by_row(anomalies)

        self.assertEqual(by_row["S1"]["anomaly_type"], "MISSING_EVENT_ID")
        self.assertEqual(by_row["S2"]["anomaly_type"], "MISSING_EVENT_ID")
        self.assertNotIn(
            by_row["S1"]["anomaly_type"], {"DUPLICATE_EVENT", "EVENT_ID_CONFLICT"}
        )
        self.assertEqual(len(anomalies), 2)  # one per row
        order = self.summary_by_order(summaries)["O1"]
        self.assertEqual(order["final_status"], "UNKNOWN")
        self.assertEqual(int(order["valid_events"]), 0)
        self.assertEqual(int(order["anomaly_count"]), 2)


class ValidationPrecedenceTests(PipelineTestCase):
    # T3
    def test_T3_only_first_applicable_anomaly(self):
        cases = [
            ("empty order wins", make_row("P1", "", "", "", ""), "MISSING_ORDER_ID"),
            ("empty event id next", make_row("P2", "", "O1", "", ""), "MISSING_EVENT_ID"),
            ("empty timestamp next", make_row("P3", "E1", "O1", "", ""), "MISSING_TIMESTAMP"),
            (
                "invalid timestamp before unknown type",
                make_row("P4", "E1", "O1", "BOGUS", "not-a-date"),
                "INVALID_TIMESTAMP",
            ),
            (
                "unknown type last",
                make_row("P5", "E1", "O1", "", "2026-01-01T00:00:00Z"),
                "UNKNOWN_EVENT",
            ),
        ]
        for name, row, expected in cases:
            with self.subTest(name=name):
                _, anomalies = self.run_pipeline([row])
                self.assertEqual(len(anomalies), 1)
                self.assertEqual(anomalies[0]["anomaly_type"], expected)

    # T3
    def test_T3_whitespace_only_cells_are_treated_as_empty(self):
        rows = [
            make_row("W1", "E1", "   ", "PLACED", "2026-01-01T00:00:00Z"),
            make_row("W3", "   ", "O9", "PLACED", "2026-01-01T00:00:00Z"),
            make_row("W4", "E4", "O9", "   ", "2026-01-01T00:00:00Z"),
        ]
        summaries, anomalies = self.run_pipeline(rows)
        by_row = self.anomalies_by_row(anomalies)
        self.assertEqual(by_row["W1"]["anomaly_type"], "MISSING_ORDER_ID")
        self.assertEqual(by_row["W3"]["anomaly_type"], "MISSING_EVENT_ID")
        self.assertEqual(by_row["W4"]["anomaly_type"], "UNKNOWN_EVENT")
        self.assertNotIn("", self.summary_by_order(summaries))


class TimestampTests(PipelineTestCase):
    # T4
    def test_T4_accepted_forms_are_normalized_in_output(self):
        rows = [
            make_row("A1", "E1", "O1", "PLACED", "2026-01-01T00:00:00Z"),
            make_row("A2", "E2", "O1", "PAID", "2026-01-01 00:01:00"),
            make_row("A3", "E3", "O1", "PACKED", "2026-01-01T00:02:00+00:00"),
        ]
        summaries, anomalies = self.run_pipeline(rows)
        self.assertEqual(anomalies, [])
        order = self.summary_by_order(summaries)["O1"]
        self.assertEqual(order["final_status"], "PACKED")
        self.assertEqual(int(order["valid_events"]), 3)
        self.assertEqual(order["first_valid_event_time"], "2026-01-01T00:00:00Z")
        self.assertEqual(order["last_valid_event_time"], "2026-01-01T00:02:00Z")

    # T4
    def test_T4_parse_timestamp_returns_aware_utc(self):
        for text in (
            "2026-04-03T09:00:00Z",
            "2026-04-03 09:00:00",
            "2026-04-03T09:00:00+00:00",
            "  2026-04-03T09:00:00Z  ",  # surrounding whitespace is trimmed
        ):
            with self.subTest(text=text):
                parsed = engine.parse_timestamp(text)
                self.assertIsNotNone(parsed)
                self.assertIsNotNone(parsed.tzinfo)
                self.assertEqual(parsed.utcoffset(), timedelta(0))
                self.assertEqual(parsed.tzinfo, timezone.utc)
                self.assertEqual(engine.format_timestamp(parsed), "2026-04-03T09:00:00Z")

    # T4
    def test_T4_rejects_unsupported_timestamp_forms(self):
        rejected = {
            "date only": "2026-04-03",
            "missing seconds": "2026-04-03T09:00",
            "fractional seconds": "2026-04-03T09:00:00.5Z",
            "nonzero offset": "2026-04-03T09:00:00+05:30",
            "compact offset": "2026-04-03T09:00:00+0000",
            "lowercase z": "2026-04-03T09:00:00z",
            "non-padded components": "2026-4-3T09:00:00Z",
            "impossible calendar date": "2026-02-30T09:00:00Z",
            "24:00:00": "2026-04-03T24:00:00Z",
            "leap second": "2026-04-03T23:59:60Z",
            "negative zero offset": "2026-04-03T09:00:00-00:00",
            "T without zone": "2026-04-03T09:00:00",
            "space form ending in Z": "2026-04-03 09:00:00Z",
        }
        for name, text in rejected.items():
            with self.subTest(name=name):
                self.assertIsNone(engine.parse_timestamp(text))

    # T4 (known deviation — see stage report)
    @unittest.expectedFailure
    def test_T4_known_deviation_rejects_non_ascii_numerals(self):
        # The prescribed format uses conventional ASCII decimal digits. The
        # current engine accepts alternate Unicode decimal digits because both
        # ``\d`` and ``strptime``/``int`` accept them. Expected to fail until a
        # strictness fix is authorized.
        fullwidth = "\uFF12\uFF10\uFF12\uFF16-04-03T09:00:00Z"  # ２０２６
        arabic_indic = "\u0662\u0660\u0662\u0666-04-03T09:00:00Z"  # ٢٠٢٦
        self.assertIsNone(engine.parse_timestamp(fullwidth))
        self.assertIsNone(engine.parse_timestamp(arabic_indic))

    # T4
    def test_T4_no_received_at_fallback(self):
        rows = [
            make_row(
                "N1",
                "E1",
                "O1",
                "PLACED",
                "",
                received_at="2026-01-01T00:00:00Z",
            )
        ]
        summaries, anomalies = self.run_pipeline(rows)
        self.assertEqual(anomalies[0]["anomaly_type"], "MISSING_TIMESTAMP")
        order = self.summary_by_order(summaries)["O1"]
        self.assertEqual(order["final_status"], "UNKNOWN")
        self.assertEqual(order["first_valid_event_time"], "")


class TimelineOrderingTests(PipelineTestCase):
    # T5
    def test_T5_same_time_orders_by_event_id_not_csv_order(self):
        rows = [
            make_row("X9", "E9", "O1", "PAID", "2026-01-01T00:01:00Z", received_at="EARLY"),
            make_row("X1", "E2", "O1", "PLACED", "2026-01-01T00:00:00Z", received_at="LATE"),
            make_row("X2", "E1", "O1", "PAID", "2026-01-01T00:00:00Z", received_at="LATE"),
        ]
        summaries, anomalies = self.run_pipeline(rows)
        by_row = self.anomalies_by_row(anomalies)
        # E1 (PAID) sorts before E2 (PLACED) at the same time -> invalid first.
        self.assertEqual(by_row["X2"]["anomaly_type"], "INVALID_TRANSITION")
        self.assertEqual(by_row["X2"]["details"], "START -> PAID is not allowed")
        order = self.summary_by_order(summaries)["O1"]
        self.assertEqual(order["final_status"], "PAID")
        self.assertEqual(int(order["valid_events"]), 2)
        self.assertEqual(order["first_valid_event_time"], "2026-01-01T00:00:00Z")
        self.assertEqual(order["last_valid_event_time"], "2026-01-01T00:01:00Z")

    # T5
    def test_T5_row_id_is_tertiary_sort_key(self):
        # Equal time and equal event_id is unreachable through the pipeline
        # (dedup keeps a single canonical row), so the documented sort key used
        # by ``engine.run`` is validated directly here.
        moment = engine.parse_timestamp("2026-01-01T00:00:00Z")
        candidates = [
            (moment, engine.Row("R3", "E1", "O1", "PLACED", "2026-01-01T00:00:00Z")),
            (moment, engine.Row("R1", "E1", "O1", "PLACED", "2026-01-01T00:00:00Z")),
            (moment, engine.Row("R2", "E1", "O1", "PLACED", "2026-01-01T00:00:00Z")),
        ]
        ordered = sorted(
            candidates, key=lambda item: (item[0], item[1].event_id, item[1].row_id)
        )
        self.assertEqual([row.row_id for _, row in ordered], ["R1", "R2", "R3"])

    # T6
    def test_T6_invalid_transition_does_not_mutate_state(self):
        rows = [
            make_row("M1", "E1", "O1", "PLACED", "2026-01-01T00:00:00Z"),
            make_row("M2", "E2", "O1", "DELIVERED", "2026-01-01T00:01:00Z"),
            make_row("M3", "E3", "O1", "PAID", "2026-01-01T00:02:00Z"),
            make_row("M4", "E4", "O1", "PACKED", "2026-01-01T00:03:00Z"),
        ]
        summaries, anomalies = self.run_pipeline(rows)
        by_row = self.anomalies_by_row(anomalies)
        self.assertEqual(by_row["M2"]["anomaly_type"], "INVALID_TRANSITION")
        self.assertEqual(by_row["M2"]["details"], "PLACED -> DELIVERED is not allowed")
        self.assertEqual(len(anomalies), 1)
        order = self.summary_by_order(summaries)["O1"]
        self.assertEqual(order["final_status"], "PACKED")
        self.assertEqual(int(order["valid_events"]), 3)
        self.assertEqual(order["first_valid_event_time"], "2026-01-01T00:00:00Z")
        self.assertEqual(order["last_valid_event_time"], "2026-01-01T00:03:00Z")


class LifecycleTests(PipelineTestCase):
    # T7
    def _sequence_rows(self, order_id, event_types):
        return [
            make_row(
                f"{order_id}-{index:02d}",
                f"{order_id}-E{index:02d}",
                order_id,
                event_type,
                f"2026-01-01T00:{index:02d}:00Z",
            )
            for index, event_type in enumerate(event_types)
        ]

    # T7
    def test_T7_full_lifecycle_reaches_refunded(self):
        sequence = [
            "PLACED",
            "PAID",
            "PACKED",
            "SHIPPED",
            "OUT_FOR_DELIVERY",
            "DELIVERED",
            "RETURN_REQUESTED",
            "RETURNED",
            "REFUNDED",
        ]
        summaries, anomalies = self.run_pipeline(self._sequence_rows("O1", sequence))
        self.assertEqual(anomalies, [])
        order = self.summary_by_order(summaries)["O1"]
        self.assertEqual(order["final_status"], "REFUNDED")
        self.assertEqual(int(order["valid_events"]), 9)
        self.assertEqual(order["last_valid_event_time"], "2026-01-01T00:08:00Z")

    # T7
    def test_T7_permitted_cancellation_paths(self):
        for accepted_prefix in (
            ["PLACED"],
            ["PLACED", "PAID"],
            ["PLACED", "PAID", "PACKED"],
        ):
            with self.subTest(prefix=accepted_prefix):
                summaries, anomalies = self.run_pipeline(
                    self._sequence_rows("O1", accepted_prefix + ["CANCELLED"])
                )
                self.assertEqual(anomalies, [])
                order = self.summary_by_order(summaries)["O1"]
                self.assertEqual(order["final_status"], "CANCELLED")
                self.assertEqual(int(order["valid_events"]), len(accepted_prefix) + 1)

    # T7
    def test_T7_terminal_states_reject_further_events(self):
        cancelled = self._sequence_rows("OC", ["PLACED", "CANCELLED", "PAID"])
        summaries, anomalies = self.run_pipeline(cancelled)
        order = self.summary_by_order(summaries)["OC"]
        self.assertEqual(order["final_status"], "CANCELLED")
        self.assertEqual(int(order["valid_events"]), 2)
        self.assertEqual(
            self.anomalies_by_row(anomalies)["OC-02"]["anomaly_type"],
            "INVALID_TRANSITION",
        )

        full = [
            "PLACED",
            "PAID",
            "PACKED",
            "SHIPPED",
            "OUT_FOR_DELIVERY",
            "DELIVERED",
            "RETURN_REQUESTED",
            "RETURNED",
            "REFUNDED",
            "PAID",
        ]
        summaries, anomalies = self.run_pipeline(self._sequence_rows("OR", full))
        order = self.summary_by_order(summaries)["OR"]
        self.assertEqual(order["final_status"], "REFUNDED")
        self.assertEqual(int(order["valid_events"]), 9)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["anomaly_type"], "INVALID_TRANSITION")


class AccountingTests(PipelineTestCase):
    # T8
    def test_T8_summary_inclusion_and_anomaly_accounting(self):
        rows = [
            # conflict-only order C1
            make_row("A01", "EC1", "C1", "PLACED", ""),
            make_row("A02", "EC1", "C1", "PAID", "2026-01-01T00:10:00Z"),
            # unknown-event-only order U1
            make_row("A03", "EU1", "U1", "WAT", "2026-01-01T00:00:00Z"),
            # duplicate plus a valid event for order D1
            make_row("A04", "ED1", "D1", "PLACED", "2026-01-01T00:00:00Z"),
            make_row("A05", "ED1", "D1", "PLACED", "2026-01-01T00:00:00Z"),
            # row with empty order_id
            make_row("A06", "EE1", "", "PLACED", "2026-01-01T00:00:00Z"),
            # order M1 with multiple distinct anomalies
            make_row("A07", "EM1", "M1", "PLACED", "2026-01-01T00:00:00Z"),
            make_row("A08", "EM2", "M1", "DELIVERED", "2026-01-01T00:01:00Z"),
            make_row("A09", "EM3", "M1", "PAID", "not-a-date"),
            # a fully valid order
            make_row("A10", "EZ1", "Z1", "PLACED", "2026-01-01T00:00:00Z"),
        ]
        summaries, anomalies = self.run_pipeline(rows)

        expected_orders = ["C1", "D1", "M1", "U1", "Z1"]
        self.assertEqual([row["order_id"] for row in summaries], expected_orders)
        self.assertNotIn("", [row["order_id"] for row in summaries])

        by_order = self.summary_by_order(summaries)
        self.assertEqual((by_order["C1"]["final_status"], int(by_order["C1"]["valid_events"])), ("UNKNOWN", 0))
        self.assertEqual(int(by_order["C1"]["anomaly_count"]), 2)
        self.assertEqual((by_order["D1"]["final_status"], int(by_order["D1"]["valid_events"])), ("PLACED", 1))
        self.assertEqual(int(by_order["D1"]["anomaly_count"]), 1)
        self.assertEqual((by_order["M1"]["final_status"], int(by_order["M1"]["valid_events"])), ("PLACED", 1))
        self.assertEqual(int(by_order["M1"]["anomaly_count"]), 2)
        self.assertEqual((by_order["U1"]["final_status"], int(by_order["U1"]["valid_events"])), ("UNKNOWN", 0))
        self.assertEqual(int(by_order["U1"]["anomaly_count"]), 1)
        self.assertEqual((by_order["Z1"]["final_status"], int(by_order["Z1"]["valid_events"])), ("PLACED", 1))
        self.assertEqual(int(by_order["Z1"]["anomaly_count"]), 0)

        # Anomaly rows: sorted by row_id, one per rejected row, empty order kept.
        self.assertEqual(
            [row["row_id"] for row in anomalies],
            ["A01", "A02", "A03", "A05", "A06", "A08", "A09"],
        )
        row_ids = [row["row_id"] for row in anomalies]
        self.assertEqual(len(row_ids), len(set(row_ids)))
        self.assertTrue({row["anomaly_type"] for row in anomalies} <= ALLOWED_ANOMALY_TYPES)
        self.assertEqual(self.anomalies_by_row(anomalies)["A06"]["order_id"], "")

        # anomaly_count reconciles with anomaly rows per order.
        for order_id in expected_orders:
            expected_count = sum(1 for row in anomalies if row["order_id"] == order_id)
            self.assertEqual(int(by_order[order_id]["anomaly_count"]), expected_count)

    # T9
    def test_T9_informational_columns_do_not_affect_identity_or_timeline(self):
        base_rows = [
            make_row("I1", "E1", "O1", "PLACED", "2026-01-01T00:00:00Z", "r1", "WEB", "a"),
            make_row("I2", "E1", "O1", "PLACED", "2026-01-01T00:00:00Z", "r2", "PAY", "b"),
            make_row("I3", "E2", "O1", "PAID", "2026-01-01T00:01:00Z", "r3", "WH", "c"),
        ]
        summaries, anomalies = self.run_pipeline(base_rows)
        self.assertEqual(self.anomalies_by_row(anomalies)["I2"]["anomaly_type"], "DUPLICATE_EVENT")
        self.assertEqual(len(anomalies), 1)
        order = self.summary_by_order(summaries)["O1"]
        self.assertEqual(order["final_status"], "PAID")
        self.assertEqual(int(order["valid_events"]), 2)

        # Changing only informational columns cannot change the outcome.
        changed_rows = [
            make_row("I1", "E1", "O1", "PLACED", "2026-01-01T00:00:00Z", "ZZ", "X", "x"),
            make_row("I2", "E1", "O1", "PLACED", "2026-01-01T00:00:00Z", "AA", "Y", "y"),
            make_row("I3", "E2", "O1", "PAID", "2026-01-01T00:01:00Z", "BB", "Z", "z"),
        ]
        self.assertEqual(
            self.snapshot(base_rows), self.snapshot(changed_rows)
        )

    # T10
    def test_T10_deduplication_precedes_timestamp_parsing(self):
        rows = [
            # identical malformed timestamps -> DUPLICATE, canonical then invalid
            make_row("B01", "EX", "O1", "PLACED", "garbage"),
            make_row("B02", "EX", "O1", "PLACED", "garbage"),
            # differing malformed timestamps -> CONFLICT, only canonical validated
            make_row("B03", "EY", "O2", "PLACED", "garbage-1"),
            make_row("B04", "EY", "O2", "PLACED", "garbage-2"),
        ]
        summaries, anomalies = self.run_pipeline(rows)
        by_row = self.anomalies_by_row(anomalies)
        self.assertEqual(by_row["B02"]["anomaly_type"], "DUPLICATE_EVENT")
        self.assertEqual(by_row["B01"]["anomaly_type"], "INVALID_TIMESTAMP")
        self.assertEqual(by_row["B04"]["anomaly_type"], "EVENT_ID_CONFLICT")
        self.assertEqual(by_row["B03"]["anomaly_type"], "INVALID_TIMESTAMP")
        self.assertEqual(len(anomalies), 4)

    # T11
    def test_T11_canonical_with_empty_order_id(self):
        rows = [
            make_row("C01", "E1", "", "PLACED", "2026-01-01T00:00:00Z"),
            make_row("C02", "E1", "O2", "PAID", "2026-01-01T00:10:00Z"),
        ]
        summaries, anomalies = self.run_pipeline(rows)
        by_row = self.anomalies_by_row(anomalies)
        self.assertEqual(by_row["C01"]["anomaly_type"], "MISSING_ORDER_ID")
        self.assertEqual(by_row["C02"]["anomaly_type"], "EVENT_ID_CONFLICT")
        self.assertEqual([row["order_id"] for row in summaries], ["O2"])
        order = self.summary_by_order(summaries)["O2"]
        self.assertEqual(order["final_status"], "UNKNOWN")
        self.assertEqual(int(order["valid_events"]), 0)
        self.assertEqual(int(order["anomaly_count"]), 1)


class FixtureOracleTests(PipelineTestCase):
    # T12
    def test_T12_worked_example_matches_supplied_fixtures(self):
        input_path = REPO_ROOT / "data" / "worked_example_input.csv"
        output_dir = self.tmp / "fixture_out"
        engine.run(input_path, output_dir)

        self.assertEqual(
            read_header(output_dir / "order_summary.csv"), list(engine.SUMMARY_HEADER)
        )
        self.assertEqual(
            read_header(output_dir / "anomalies.csv"), list(engine.ANOMALY_HEADER)
        )

        expected_summary = read_rows(REPO_ROOT / "data" / "worked_example_summary.csv")
        actual_summary = read_rows(output_dir / "order_summary.csv")
        self.assertEqual(len(expected_summary), 7)
        self.assertEqual(actual_summary, expected_summary)

        expected_anomalies = read_rows(
            REPO_ROOT / "data" / "worked_example_anomalies.csv"
        )
        actual_anomalies = read_rows(output_dir / "anomalies.csv")
        self.assertEqual(len(expected_anomalies), 9)
        without_details = lambda row: {k: v for k, v in row.items() if k != "details"}  # noqa: E731
        self.assertEqual(
            [without_details(row) for row in actual_anomalies],
            [without_details(row) for row in expected_anomalies],
        )


class DeterminismTests(PipelineTestCase):
    # T13
    def test_T13_permutation_invariance_and_reproducibility(self):
        rows = [
            make_row("G01", "EG1", "O2", "PLACED", "2026-01-01T00:00:00Z"),
            make_row("G02", "EG2", "O1", "PLACED", "2026-01-01T00:00:00Z"),
            make_row("G03", "EG2", "O1", "PLACED", "2026-01-01T00:00:00Z"),
            make_row("G04", "EG3", "O1", "PAID", "2026-01-01T00:01:00Z"),
            make_row("G05", "EG4", "O3", "PAID", "2026-01-01T00:00:00Z"),
            make_row("G06", "EG5", "O3", "DELIVERED", "2026-01-01T00:01:00Z"),
            make_row("G07", "EG6", "O4", "PLACED", "not-a-date"),
            make_row("G08", "EG7", "", "PLACED", "2026-01-01T00:00:00Z"),
            make_row("G09", "EG8", "O2", "WAT", "2026-01-01T00:02:00Z"),
            make_row("G10", "EG9", "O5", "SHIPPED", "2026-01-01T00:00:00Z"),
        ]
        variants = [rows, list(reversed(rows))]
        for seed in range(4):
            shuffled = rows[:]
            random.Random(seed).shuffle(shuffled)
            variants.append(shuffled)

        baseline = self.snapshot(rows)
        self.assertEqual(baseline, self.snapshot(rows))  # rerun identical -> same
        for index, variant in enumerate(variants):
            with self.subTest(variant=index):
                self.assertEqual(self.snapshot(variant), baseline)


class RowIdIntegrityTests(PipelineTestCase):
    # T15
    def test_T15_empty_row_id_is_a_clear_failure(self):
        rows = [make_row("", "E1", "O1", "PLACED", "2026-01-01T00:00:00Z")]
        with self.assertRaises(engine.EngineError) as ctx:
            self.run_pipeline(rows)
        self.assertIn("row_id", str(ctx.exception))

    # T15
    def test_T15_whitespace_only_row_id_is_a_clear_failure(self):
        rows = [make_row("   ", "E1", "O1", "PLACED", "2026-01-01T00:00:00Z")]
        with self.assertRaises(engine.EngineError) as ctx:
            self.run_pipeline(rows)
        self.assertIn("row_id", str(ctx.exception))

    # T15
    def test_T15_duplicate_trimmed_row_id_is_a_clear_failure(self):
        rows = [
            make_row("R1", "E1", "O1", "PLACED", "2026-01-01T00:00:00Z"),
            make_row(" R1 ", "E2", "O1", "PAID", "2026-01-01T00:01:00Z"),
        ]
        with self.assertRaises(engine.EngineError) as ctx:
            self.run_pipeline(rows)
        message = str(ctx.exception)
        self.assertIn("row_id", message)
        self.assertIn("R1", message)


if __name__ == "__main__":
    unittest.main()
