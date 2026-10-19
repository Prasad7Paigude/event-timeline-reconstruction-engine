"""CLI-level tests for ``main.py`` using subprocesses and temporary paths.

These verify process exit status, stderr messaging, and output contract for the
command-line entry point, without touching the large datasets.
"""

from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN = REPO_ROOT / "main.py"
HEADER = [
    "row_id",
    "event_id",
    "order_id",
    "event_type",
    "event_time",
    "received_at",
    "source_system",
    "metadata",
]
SUMMARY_HEADER = [
    "order_id",
    "final_status",
    "valid_events",
    "anomaly_count",
    "first_valid_event_time",
    "last_valid_event_time",
]
ANOMALY_HEADER = ["row_id", "order_id", "event_id", "anomaly_type", "details"]


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(MAIN), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


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


def valid_rows():
    return [
        ["V1", "E1", "K1", "PLACED", "2026-01-01T00:00:00Z", "r", "WEB", "m"],
        ["V2", "E2", "K1", "PAID", "2026-01-01T00:01:00Z", "r", "WEB", "m"],
    ]


class CliErrorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    # T14
    def test_T14_missing_required_arguments_is_nonzero(self):
        result = run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)

    # T14
    def test_T14_nonexistent_input_reports_clearly(self):
        result = run_cli(
            "--input", str(self.tmp / "nope.csv"), "--output-dir", str(self.tmp / "out")
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)

    # T14
    def test_T14_missing_required_column_reports_clearly(self):
        bad = self.tmp / "bad.csv"
        with open(bad, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["row_id", "event_id", "event_time"])
            writer.writerow(["R1", "E1", "2026-01-01T00:00:00Z"])
        result = run_cli("--input", str(bad), "--output-dir", str(self.tmp / "out"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing required column", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    # T14
    def test_T14_output_path_is_existing_file_reports_clearly(self):
        good = self.tmp / "good.csv"
        write_csv(good, valid_rows())
        collide = self.tmp / "collide"
        collide.write_text("not a directory", encoding="utf-8")
        result = run_cli("--input", str(good), "--output-dir", str(collide))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("output", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)

    # T14
    def test_T14_unterminated_quote_is_rejected_without_traceback(self):
        bad = self.tmp / "bad_quote.csv"
        bad.write_text(
            "row_id,event_id,order_id,event_type,event_time,received_at,source_system,metadata\n"
            'U1,e1,O1,PLACED,2026-01-01T00:00:00Z,r,s,"unterminated\n',
            encoding="utf-8",
        )
        result = run_cli("--input", str(bad), "--output-dir", str(self.tmp / "out"))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertTrue(result.stderr.strip())

    # T14
    def test_T14_invalid_utf8_is_rejected_without_traceback(self):
        bad = self.tmp / "bad_utf8.csv"
        header = (
            "row_id,event_id,order_id,event_type,event_time,received_at,source_system,metadata\n"
        ).encode("ascii")
        bad.write_bytes(header + b"X1,e1,O1,PLACED,2026-01-01T00:00:00Z,r,s," + bytes([0xFF, 0xFE]) + b"\n")
        result = run_cli("--input", str(bad), "--output-dir", str(self.tmp / "out"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("UTF-8", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class CliSuccessTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    # T14
    def test_T14_valid_nested_output_dir_is_created(self):
        good = self.tmp / "good.csv"
        write_csv(good, valid_rows())
        out = self.tmp / "a" / "b" / "c"
        result = run_cli("--input", str(good), "--output-dir", str(out))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue((out / "order_summary.csv").is_file())
        self.assertTrue((out / "anomalies.csv").is_file())

    # T14
    def test_T14_arbitrary_valid_csv_produces_exact_headers_and_values(self):
        good = self.tmp / "good.csv"
        write_csv(good, valid_rows())
        out = self.tmp / "out"
        result = run_cli("--input", str(good), "--output-dir", str(out))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(read_header(out / "order_summary.csv"), SUMMARY_HEADER)
        self.assertEqual(read_header(out / "anomalies.csv"), ANOMALY_HEADER)
        summaries = read_rows(out / "order_summary.csv")
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["order_id"], "K1")
        self.assertEqual(summaries[0]["final_status"], "PAID")
        self.assertEqual(summaries[0]["valid_events"], "2")

    # T14
    def test_T14_quoted_commas_and_embedded_newlines_are_handled(self):
        rows = [
            ["Q1", "E1", "K1", "PLACED", "2026-01-01T00:00:00Z", "r", "WEB", "a, b"],
            ["Q2", "E2", "K1", "PAID", "2026-01-01T00:01:00Z", "r", "WEB", "line1\nline2"],
        ]
        good = self.tmp / "quoted.csv"
        write_csv(good, rows)
        out = self.tmp / "out"
        result = run_cli("--input", str(good), "--output-dir", str(out))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        summaries = read_rows(out / "order_summary.csv")
        self.assertEqual(summaries[0]["final_status"], "PAID")
        self.assertEqual(read_rows(out / "anomalies.csv"), [])

    # T14
    def test_T14_header_only_input_writes_empty_outputs(self):
        good = self.tmp / "empty.csv"
        write_csv(good, [])
        out = self.tmp / "out"
        result = run_cli("--input", str(good), "--output-dir", str(out))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(read_header(out / "order_summary.csv"), SUMMARY_HEADER)
        self.assertEqual(read_header(out / "anomalies.csv"), ANOMALY_HEADER)
        self.assertEqual(read_rows(out / "order_summary.csv"), [])
        self.assertEqual(read_rows(out / "anomalies.csv"), [])


if __name__ == "__main__":
    unittest.main()
