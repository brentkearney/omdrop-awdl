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
        # The validators are what every path under test calls first.
        names = ("is_uint", "is_duration") + tuple(n for n in names if n not in ("is_uint", "is_duration"))
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

    def test_a_guessed_token_is_refused_and_leaves_the_real_one(self):
        (self.run_dir / "supervisor.token").write_text("0" * 32)
        result = self.harness(
            f"OMDROP_SUPERVISOR_TOKEN={'1' * 32} supervise_entry 0 0",
            "is_uint", "supervise", "supervise_entry")
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / "spawned").exists())
        self.assertEqual((self.run_dir / "supervisor.token").read_text(), "0" * 32)

    def test_a_direct_call_during_a_start_does_not_break_it(self):
        # cmd_start has written the token and is spawning its child; another
        # user's `pkexec ... __supervise` lands in between.
        (self.run_dir / "supervisor.token").write_text("cd" * 16)
        (self.run_dir / "until").write_text("1")
        direct = self.harness("supervise_entry 1 600", "is_uint", "supervise", "supervise_entry")
        self.assertEqual(direct.returncode, 2)
        spawned = self.harness(
            f"CONF={self.root}/conf LIB=/nonexistent IF=awdl0 ADV_FLAGS=137 "
            f"OMDROP_SUPERVISOR_TOKEN={'cd' * 16} supervise_entry 1 600",
            "is_uint", "supervise", "supervise_entry")
        self.assertEqual(spawned.returncode, 0, spawned.stderr)
        self.assertIn("spawn mdns", (self.root / "spawned").read_text())
        self.assertFalse((self.run_dir / "supervisor.token").exists())

    def test_numbers_bash_would_misread_are_refused_before_arithmetic(self):
        # 08 and 09 are invalid octal, 010 would be 8, and 20 digits wrap.
        for value in ("08", "09", "010", "9" * 20):
            with self.subTest(value=value):
                start = self.harness(f"cmd_start {value}", "is_uint", "cmd_start")
                self.assertEqual(start.returncode, 2, start.stderr)
                self.assertIn("usage", start.stderr)
                for args in (f"{value} 600", f"0 {value}"):
                    (self.root / "spawned").unlink(missing_ok=True)
                    (self.run_dir / "supervisor.token").write_text("ab" * 16)
                    supervised = self.harness(
                        f"OMDROP_SUPERVISOR_TOKEN={'ab' * 16} supervise_entry {args}",
                        "is_uint", "supervise", "supervise_entry")
                    self.assertEqual(supervised.returncode, 2, supervised.stderr)
                    self.assertIn("must be a number", supervised.stderr)
                    self.assertFalse((self.root / "spawned").exists())

    def test_the_longest_duration_gives_a_deadline_the_child_accepts(self):
        # Cold start: cmd_start computes now + secs and passes both to the
        # child, which checks the deadline with is_uint and secs with
        # is_duration. The longest accepted duration must survive both.
        (self.run_dir / "supervisor.token").write_text("ab" * 16)
        (self.run_dir / "until").write_text("1")
        result = self.harness(
            f"CONF={self.root}/conf LIB=/nonexistent IF=awdl0 ADV_FLAGS=137 "
            f"OMDROP_SUPERVISOR_TOKEN={'ab' * 16} supervise_entry $(( $(date +%s) + 999999999 )) 999999999",
            "supervise", "supervise_entry")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_one_second_longer_is_refused_before_any_work(self):
        result = self.harness(f"cmd_start 1000000000; touch {self.marker}", "cmd_start")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("at most 999999999", result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.run_dir / "start.lock").exists())

    def test_extending_a_live_window_writes_a_deadline_the_supervisor_accepts(self):
        (self.run_dir / "until").write_text("0")
        extend = self.harness("supervisor_alive(){ return 0; }; cmd_start 999999999", "cmd_start")
        self.assertEqual(extend.returncode, 5, extend.stderr)
        deadline = (self.run_dir / "until").read_text().strip()
        check = self.harness(f"is_uint {deadline} && echo accepted")
        self.assertEqual(check.stdout.strip(), "accepted", deadline)
        (self.run_dir / "until").write_text("0")
        refused = self.harness("supervisor_alive(){ return 0; }; cmd_start 1000000000", "cmd_start")
        self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertEqual((self.run_dir / "until").read_text(), "0")

    def test_read_refuses_iovars_outside_awdl(self):
        for name in ("wsec_key", "pmk", "sae_password", "awdl"):
            with self.subTest(name=name):
                result = self.harness(
                    f"iovar_get_len(){{ touch {self.marker}; }}; cmd_read {name}", "is_uint", "cmd_read")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("limited to awdl_", result.stderr)
                self.assertFalse(self.marker.exists())

    def test_read_still_reaches_the_firmware_for_awdl_iovars(self):
        for name in ("awdl_peer_table", "ver", "cap"):
            with self.subTest(name=name):
                result = self.harness(
                    f"iovar_get_len(){{ echo \"$1 $2\" >> {self.marker}; echo 0; }}; cmd_read {name} 64",
                    "is_uint", "cmd_read")
                self.assertEqual(result.returncode, 4, result.stderr)
                self.assertIn(f"{name} 64", self.marker.read_text())

    def test_read_refuses_a_length_bash_would_misread(self):
        result = self.harness(f"iovar_get_len(){{ touch {self.marker}; }}; cmd_read awdl_stats 09",
                              "is_uint", "cmd_read")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.marker.exists())

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
