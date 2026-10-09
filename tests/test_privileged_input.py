"""Caller input never reaches bash arithmetic in the polkit-reachable helper.

omdrop-discoverable runs as root for any active user, with any argv. Bash
arithmetic evaluates array subscripts, so a value like a[$(cmd)] runs cmd;
`__supervise` passed its seconds argument straight into $(( )). These tests
run the real functions, extracted from the script, as an unprivileged user
with need_root stubbed out, and check that a payload never executes.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISCOVERABLE = ROOT / "userspace" / "omdrop-discoverable"


def function(source, name):
    start = source.index(f"\n{name}(){{") + 1
    return source[start:source.index("\n}\n", start) + 3]


class PrivilegedInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.marker = self.root / "executed"
        self.payload = f"a[$(touch {self.marker})]"
        self.source = DISCOVERABLE.read_text()

    def harness(self, body, *names):
        fragments = "\n".join(function(self.source, n) for n in names)
        script = f'''
set -uo pipefail
RUN={self.run_dir}
log(){{ printf '%s\\n' "$*" >&2; }}
die(){{ local rc=$1; shift; log "$*"; exit "$rc"; }}
need_root(){{ :; }}
now(){{ date +%s; }}
# Anything past validation would start radio children; record it instead.
spawn(){{ echo "spawn $1" >> {self.root}/spawned; }}
kill_children(){{ :; }}
track_election(){{ :; }}
rx_watch(){{ :; }}
mdns_reclaim(){{ :; }}
{fragments}
{body}
'''
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    def assert_not_executed(self, result):
        self.assertFalse(self.marker.exists(), f"payload ran: {result.stderr}")

    def test_a_direct_supervise_call_is_refused(self):
        # What `pkexec omdrop-discoverable __supervise 0 <payload>` reaches:
        # no token in the environment, because pkexec strips it.
        (self.run_dir / "until").write_text("0")
        result = self.harness(f"supervise_entry 0 '{self.payload}'",
                              "is_uint", "supervise", "supervise_entry")
        self.assertEqual(result.returncode, 2)
        self.assertIn("internal", result.stderr)
        self.assert_not_executed(result)
        self.assertFalse((self.root / "spawned").exists())

    def test_a_guessed_token_is_refused(self):
        (self.run_dir / "supervisor.token").write_text("0" * 32)
        result = self.harness(
            f"OMDROP_SUPERVISOR_TOKEN={'1' * 32} supervise_entry 0 0",
            "is_uint", "supervise", "supervise_entry")
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / "spawned").exists())
        # A failed attempt also burns the token, so it cannot be retried.
        self.assertFalse((self.run_dir / "supervisor.token").exists())

    def test_the_spawned_supervisor_still_validates_its_seconds(self):
        # Even with the right token, a non-number never reaches arithmetic,
        # including the BLE wake's $(( secs > 0 ? secs : 86400 )).
        (self.run_dir / "supervisor.token").write_text("ab" * 16)
        result = self.harness(
            f"OMDROP_SUPERVISOR_TOKEN={'ab' * 16} supervise_entry 0 '{self.payload}'",
            "is_uint", "supervise", "supervise_entry")
        self.assertEqual(result.returncode, 2)
        self.assert_not_executed(result)
        self.assertFalse((self.root / "spawned").exists())

    def test_the_spawned_supervisor_runs_with_a_valid_token(self):
        # The legitimate path: cmd_start's token, numeric arguments, and an
        # already-expired deadline so the loop exits at once.
        (self.run_dir / "supervisor.token").write_text("cd" * 16)
        (self.run_dir / "until").write_text("1")
        result = self.harness(
            f"CONF={self.root}/conf LIB=/nonexistent IF=awdl0 ADV_FLAGS=137 "
            f"OMDROP_SUPERVISOR_TOKEN={'cd' * 16} supervise_entry 1 600",
            "is_uint", "supervise", "supervise_entry")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("spawn mdns", (self.root / "spawned").read_text())
        self.assertFalse((self.run_dir / "supervisor.token").exists())

    def test_a_tampered_deadline_file_is_not_evaluated(self):
        (self.run_dir / "supervisor.token").write_text("ef" * 16)
        (self.run_dir / "until").write_text(self.payload)
        result = self.harness(
            f"CONF={self.root}/conf LIB=/nonexistent IF=awdl0 ADV_FLAGS=137 "
            f"OMDROP_SUPERVISOR_TOKEN={'ef' * 16} supervise_entry 0 0",
            "is_uint", "supervise", "supervise_entry")
        self.assert_not_executed(result)

    def test_diag_refuses_a_non_numeric_duration(self):
        result = self.harness(f"cmd_diag '{self.payload}'", "is_uint", "cmd_diag")
        self.assertEqual(result.returncode, 2)
        self.assert_not_executed(result)

    def test_a_tampered_receive_baseline_is_not_evaluated(self):
        (self.run_dir / "rx_base").write_text(self.payload)
        result = self.harness(
            "IF=nonexistent0; rx_packets(){ echo 5; }; rx_mark(){ :; }; rx_watch",
            "is_uint", "rx_watch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_not_executed(result)


if __name__ == "__main__":
    unittest.main()
