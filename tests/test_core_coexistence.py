"""omdrop-discoverable beside omdrop-core's root helper, and its argv.

omdrop-core's omdrop-radio shares /run/awdl-discoverable with this helper. It
writes `supervisor` as `<pid> <start time>` and `pids` lines as
`<pid> <tag> <start time>`, and its supervisor runs as root with argv
[/usr/lib/omdrop-core/omdrop-radio, __supervise, ...]. The 0.9.x helper must
recognise that window instead of killing its children and opening a second
one, and must signal a recorded pid only while it still names the process
that was recorded.

Like the other suites, these run the real functions, extracted from the
script, as an unprivileged user against processes the test starts. The
dispatcher tests run the script itself, which refuses at the root check
before touching anything.
"""
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISCOVERABLE = ROOT / "userspace" / "omdrop-discoverable"
BROADCOM = ROOT / "userspace" / "omdrop-broadcom.sh"

SLEEPER = "import time\ntime.sleep(30)\n"

# omdrop-core's supervisor as far as stop can see it: one child, recorded in a
# file, and a teardown on TERM that takes a second before stopping the child.
TEARING_DOWN = '''import os, signal, subprocess, sys, time
def term(*_):
    time.sleep(1)
    child.terminate()
    child.wait()
    sys.exit(0)
signal.signal(signal.SIGTERM, term)
child = subprocess.Popen(["sleep", "30"])
with open("child.tmp", "w") as f:
    f.write(f"{child.pid}\\n")
os.replace("child.tmp", "child")
time.sleep(30)
'''


def function(source, name):
    # A function as written in the script: one line, or up to its closing brace.
    start = source.index(f"\n{name}(){{") + 1
    end = source.index("\n", start)
    if not source[start:end].rstrip().endswith("}"):
        end = source.index("\n}\n", start) + 2
    return source[start:end + 1]


def start_time(pid):
    stat = Path(f"/proc/{pid}/stat").read_text()
    return stat.rsplit(")", 1)[1].split()[19]


class CoexistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.core = self.root / "omdrop-core" / "omdrop-radio"
        self.core.parent.mkdir()
        # Python runs argv[1] as its script, so a process can be given any
        # argv[0] and a script named __supervise: exactly core's argv.
        self.core.write_text(SLEEPER)
        (self.root / "__supervise").write_text(SLEEPER)
        self.processes = []
        self.addCleanup(self.reap)
        self.source = DISCOVERABLE.read_text()

    def reap(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait()

    def spawn(self, *argv):
        process = subprocess.Popen(list(argv), executable=sys.executable, cwd=self.root)
        self.processes.append(process)
        time.sleep(0.05)
        return process

    def core_supervisor(self):
        return self.spawn(str(self.core), "__supervise", "0", "0")

    def lifecycle(self):
        start = self.source.index("supervisor_pid(){")
        return self.source[start:self.source.index("# --- commands", start)]

    def harness(self, body, *names, owner=None):
        lib = BROADCOM.read_text()
        fragments = "\n".join(function(lib if n in ("is_uint", "is_duration", "run_dir") else self.source, n)
                              for n in names)
        script = f'''set -uo pipefail
RUN={self.run_dir}
SELF={self.root}/omdrop-discoverable
log(){{ printf '%s\\n' "$*" >&2; }}
die(){{ local rc=$1; shift; log "$*"; exit "$rc"; }}
need_root(){{ :; }}
{self.lifecycle()}
CORE_RADIO={self.core}
CORE_UID={os.getuid() if owner is None else owner}
{fragments}
{body}
'''
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)

    def test_core_supervisor_is_matched_by_position_and_owner(self):
        core = self.core_supervisor()
        # The pair later in argv is what a forged argv tail looks like.
        self.spawn("decoy", str(self.core), "__supervise")
        result = self.harness("core_supervisor_pids")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), [str(core.pid)])

    def test_a_process_gone_mid_scan_prints_nothing(self):
        # /proc/[0-9]* is globbed before each entry is read, so a process can
        # exit in between. That is a normal "no", not an error for status to
        # print.
        result = self.harness(f"is_core_supervisor {self.root}/gone")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")

    def test_core_supervisor_must_run_as_root(self):
        # As shipped, CORE_UID is 0, and the tester's process is not root's.
        self.core_supervisor()
        result = self.harness("core_supervisor_pids", owner=0 if os.getuid() else 1)
        self.assertEqual(result.stdout.split(), [])

    def test_core_supervisor_file_reads_as_a_live_window(self):
        core = self.core_supervisor()
        (self.run_dir / "supervisor").write_text(f"{core.pid} {start_time(core.pid)}\n")
        result = self.harness("supervisor_pid; echo; supervisor_alive && echo alive")
        self.assertEqual(result.stdout.split(), [str(core.pid), "alive"], result.stderr)

    def test_a_dead_core_supervisor_is_not_a_live_window(self):
        core = self.core_supervisor()
        line = f"{core.pid} {start_time(core.pid)}\n"
        core.kill()
        core.wait()
        (self.run_dir / "supervisor").write_text(line)
        result = self.harness("supervisor_alive")
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_start_leaves_a_core_window_alone(self):
        core = self.core_supervisor()
        (self.run_dir / "supervisor").write_text(f"{core.pid} {start_time(core.pid)}\n")
        (self.run_dir / "until").write_text("0")
        (self.run_dir / "reason").write_text("ok")
        marker = self.root / "touched"
        result = self.harness(
            f"kill_children(){{ echo kill >> {marker}; }}; radio_up(){{ echo up >> {marker}; }}; "
            "cmd_start 600", "is_uint", "is_duration", "cmd_start")
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertIn("Already discoverable through omdrop-core",
                      result.stderr.splitlines()[-1])
        self.assertFalse(marker.exists())
        self.assertEqual((self.run_dir / "until").read_text(), "0")
        self.assertEqual((self.run_dir / "reason").read_text(), "ok")

    def test_stop_ends_a_core_window_before_returning(self):
        (self.root / "__supervise").write_text(TEARING_DOWN)
        core = self.core_supervisor()
        recorded = self.root / "child"
        deadline = time.monotonic() + 10
        while not recorded.exists():
            self.assertLess(time.monotonic(), deadline, "the fake supervisor never started its child")
            time.sleep(0.05)
        child = int(recorded.read_text())
        self.addCleanup(self.kill_sleeper, child)
        (self.run_dir / "supervisor").write_text(f"{core.pid} {start_time(core.pid)}\n")
        (self.run_dir / "until").write_text("0")
        (self.run_dir / "reason").write_text("ok")
        # pgrep would look for `bash start` across the machine: not ours.
        stop = self.harness("pgrep(){ :; }; mdns_reclaim(){ :; }; cmd_stop",
                            "start_time", "same_process", "kill_children", "cmd_stop")
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertIsNotNone(core.poll(), "stop returned while omdrop-core's supervisor still ran")
        self.assertFalse(self.sleeper_running(child), "core's child outlived stop")
        self.assertFalse((self.run_dir / "until").exists())
        self.assertEqual((self.run_dir / "reason").read_text(), "stopped")
        # The next start gets past the window checks to the radio.
        marker = self.root / "radio_up"
        start = self.harness(f"radio_up(){{ echo up >> {marker}; exit 0; }}; cmd_start 600",
                             "is_uint", "is_duration", "start_time", "same_process",
                             "kill_children", "cmd_start")
        self.assertEqual(start.returncode, 0, start.stderr)
        self.assertTrue(marker.exists(), start.stderr)

    @staticmethod
    def sleeper_running(pid):
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            return False
        return state != "Z" and cmdline == b"sleep\x0030\x00"

    def kill_sleeper(self, pid):
        if self.sleeper_running(pid):
            os.kill(pid, signal.SIGKILL)

    def kill_children(self, lines):
        (self.run_dir / "pids").write_text("".join(lines))
        return self.harness("kill_children", "kill_children", "start_time", "same_process")

    def test_kill_children_signals_a_timed_line_only_while_it_names_that_process(self):
        recorded = self.spawn("child", str(self.core))
        reused = self.spawn("child", str(self.core))
        legacy = self.spawn("child", str(self.core))
        for process in (recorded, reused, legacy):
            self.assertIsNone(process.poll())
        wrong = str(int(start_time(reused.pid)) + 1)
        result = self.kill_children([
            f"{recorded.pid} mdns {start_time(recorded.pid)}\n",
            f"{reused.pid} peers {wrong}\n",
            f"{legacy.pid} ble\n",
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        for process in (recorded, legacy):
            self.assertEqual(process.poll(), -15, "a recorded child was not stopped")
        self.assertIsNone(reused.poll(), "a process the line no longer names was signalled")
        self.assertFalse((self.run_dir / "pids").exists())

    def test_only_positive_pids_are_signalled(self):
        for pid in ("0", "-1", "01", "a[$(id)]", "12345678901", ""):
            with self.subTest(pid=pid):
                result = self.harness(f"same_process '{pid}' ''", "start_time", "same_process")
                self.assertEqual(result.returncode, 1, result.stderr)

    def test_start_time_reads_past_a_command_name_with_spaces(self):
        # The command name is the executable's base name, parentheses and all.
        link = self.root / "a) b ("
        link.symlink_to("/usr/bin/sleep")
        process = subprocess.Popen([str(link), "30"])
        self.processes.append(process)
        time.sleep(0.05)
        self.assertIn("(a) b ()", Path(f"/proc/{process.pid}/stat").read_text())
        result = self.harness(f"start_time {process.pid}", "start_time")
        self.assertEqual(result.stdout, start_time(process.pid))

    def test_repair_discards_a_pids_file_others_could_have_rewritten(self):
        # An older helper under umask 000 left `pids` 0666 (the tester stands
        # in for root as its owner). Anyone could have replaced our line with a
        # bare pid naming an unrelated root process: no start time, so
        # kill_children would signal whatever has that pid.
        unrelated = subprocess.Popen(["sleep", "30"])
        self.processes.append(unrelated)
        pids = self.run_dir / "pids"
        pids.write_text(f"{unrelated.pid} mdns\n")
        pids.chmod(0o666)
        kept = self.run_dir / "until"
        kept.write_text("0")
        kept.chmod(0o644)
        result = self.harness("run_dir && kill_children",
                              "run_dir", "kill_children", "start_time", "same_process")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("repaired", result.stderr)
        self.assertFalse(pids.exists())
        self.assertIsNone(unrelated.poll(), "a pid from an untrusted pids file was signalled")
        self.assertEqual(kept.read_text(), "0")


class DispatchTests(unittest.TestCase):
    """Extra arguments are refused before any command runs."""

    def run_helper(self, *args):
        return subprocess.run([str(DISCOVERABLE), *args], capture_output=True, text=True,
                              timeout=30, cwd="/")

    def test_extra_arguments_are_a_usage_error(self):
        cases = [("stop", "x"), ("peers", "x"), ("diag", "600", "/usr/lib/omdrop/omdrop-discoverable"),
                 ("diag", "600", "/usr/lib/omdrop-core/omdrop-radio", "__supervise"),
                 ("start", "5", "x"), ("status", "--json", "x"), ("read", "awdl_stats", "64", "x"),
                 ("trace", "on", "x"), ("__supervise", "0", "0", "x")]
        for argv in cases:
            with self.subTest(argv=argv):
                result = self.run_helper(*argv)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertTrue(result.stderr.startswith("usage: "), result.stderr)
                self.assertNotIn("needs root", result.stderr)

    @unittest.skipIf(os.geteuid() == 0, "relies on the root check stopping each command")
    def test_the_arguments_callers_pass_still_reach_their_command(self):
        for argv in (("stop",), ("peers",), ("diag",), ("diag", "5"), ("start", "600"),
                     ("trace", "on")):
            with self.subTest(argv=argv):
                result = self.run_helper(*argv)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("needs root", result.stderr)


if __name__ == "__main__":
    unittest.main()
