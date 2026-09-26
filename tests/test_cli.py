"""CLI: sim writes rows, report prints, check exit codes."""

import json
import os
import sys
import tempfile
import unittest
from io import StringIO

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cli
from sidecar import TraceStore


def run_cli(*argv):
    old = sys.argv
    sys.argv = ["cli.py", *argv]
    buf = StringIO()
    old_out = sys.stdout
    sys.stdout = buf
    code = 0
    try:
        cli.main()
    except SystemExit as e:
        code = e.code or 0
    finally:
        sys.argv = old
        sys.stdout = old_out
    return code, buf.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.db")
        self.cfg = os.path.join(self.tmp.name, "budgets.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _sim(self, **kw):
        args = ["sim", "--db", self.db, "--days", "2", "--requests-per-day", "10"]
        for k, v in kw.items():
            args += [f"--{k}", str(v)]
        return run_cli(*args)

    def test_sim_writes_rows(self):
        code, out = self._sim()
        self.assertEqual(code, 0)
        self.assertIn("simulated 20 requests", out)
        self.assertEqual(TraceStore(self.db).count(), 20)

    def test_report_prints_tables(self):
        self._sim()
        code, out = run_cli("report", "--db", self.db)
        self.assertEqual(code, 0)
        self.assertIn("cost report", out)
        self.assertIn("acme", out)
        self.assertIn("gpt-4o-mini", out)

    def test_report_csv_export(self):
        self._sim()
        csv_path = os.path.join(self.tmp.name, "out.csv")
        code, _ = run_cli("report", "--db", self.db, "--csv", csv_path)
        self.assertEqual(code, 0)
        with open(csv_path, encoding="utf-8") as fh:
            self.assertEqual(len(fh.read().strip().splitlines()), 21)

    def _write_cfg(self, soft, hard):
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump({"global": {"soft": soft, "hard": hard}}, fh)

    def test_check_exit_0_within_budget(self):
        self._sim()
        self._write_cfg(soft=1000.0, hard=2000.0)
        code, out = run_cli("check", "--db", self.db, "--config", self.cfg)
        self.assertEqual(code, 0)
        self.assertIn("state=ok", out)

    def test_check_exit_1_soft_crossed(self):
        self._sim()
        self._write_cfg(soft=0.0001, hard=1000.0)
        code, _ = run_cli("check", "--db", self.db, "--config", self.cfg)
        self.assertEqual(code, 1)

    def test_check_exit_2_hard_crossed(self):
        self._sim()
        self._write_cfg(soft=0.0001, hard=0.0002)
        code, _ = run_cli("check", "--db", self.db, "--config", self.cfg)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
