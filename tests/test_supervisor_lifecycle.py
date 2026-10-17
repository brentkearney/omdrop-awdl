import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISCOVERABLE = ROOT / "userspace" / "omdrop-discoverable"


class SupervisorLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / "run"
        self.run.mkdir()
        self.helper = self.root / "omdrop-discoverable"
        self.helper.write_text("#!/usr/bin/env bash\nsleep 30\n")
        self.helper.chmod(0o755)
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            process.terminate()
        for process in self.processes:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def spawn(self, *args):
        process = subprocess.Popen([self.helper, *args])
        self.processes.append(process)
        time.sleep(0.02)
        return process

    def lifecycle_fragment(self):
        source = DISCOVERABLE.read_text()
        start = source.index("supervisor_pid(){")
        return source[start:source.index("# --- commands", start)]

    def run_fragment(self, body, supervisor_uid=os.getuid()):
        uid_line = "" if supervisor_uid is None else f"SUPERVISOR_UID={supervisor_uid}\n"
        harness = f'''\nset -uo pipefail\nRUN={self.run}\nSELF={self.helper}\n{uid_line}# The workstation's grep replacement cannot search /proc/*/cmdline. The\n# lifecycle must not depend on grep accepting binary procfs files.\ngrep(){{ return 1; }}\n{self.lifecycle_fragment()}\n{body}\n'''
        return subprocess.run(["bash", "-c", harness], capture_output=True, text=True)

    def test_recorded_supervisor_is_recognized_without_grep(self):
        supervisor = self.spawn("__supervise", "0", "0")
        (self.run / "supervisor").write_text(str(supervisor.pid))

        result = self.run_fragment("supervisor_alive")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_owned_supervisors_are_enumerated_for_cleanup(self):
        first = self.spawn("__supervise", "0", "0")
        second = self.spawn("__supervise", "0", "0")
        unrelated = subprocess.Popen([self.helper, "start", "0"])
        self.processes.append(unrelated)
        time.sleep(0.02)

        result = self.run_fragment("supervisor_pids")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            set(result.stdout.split()),
            {str(first.pid), str(second.pid)},
        )

    def test_stop_waits_for_a_supervisor_still_tearing_down(self):
        # A TERMed supervisor runs its teardown before exiting. stop used to
        # return after a fixed second, so a start right after it raced the old
        # supervisor's teardown and lost its window.
        self.helper.write_text(
            "#!/usr/bin/env bash\n"
            "trap 'sleep 2; exit 0' TERM\n"
            "while :; do sleep 0.1 & wait $!; done\n")
        supervisor = self.spawn("__supervise", "0", "0")

        started = time.monotonic()
        result = self.run_fragment(
            f"kill -TERM {supervisor.pid}; wait_supervisors_gone")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(time.monotonic() - started, 1.5)
        self.assertIsNotNone(supervisor.poll())

    def test_the_supervisor_acts_on_term_during_its_interval_sleep(self):
        # bash defers a trap until the foreground command ends, so a TERM
        # arriving during the interval sleep used to wait out the whole sleep
        # -- long enough for a new window to start and be torn down by it.
        source = DISCOVERABLE.read_text()
        start = source.index("supervise(){")
        supervise = source[start:source.index("\n}\n", start) + 3]
        (self.run / "until").write_text("0")
        harness = f'''
set -uo pipefail
RUN={self.run} IF=awdl0 LIB=/nonexistent CONF=/nonexistent ADV_FLAGS=0
ADV_COUNT=1 ADV_INTERVAL=30
log(){{ :; }}; spawn(){{ :; }}; python3(){{ :; }}; ip(){{ :; }}
track_election(){{ :; }}; rx_watch(){{ :; }}; mdns_reclaim(){{ :; }}
kill_children(){{ :; }}; is_duration(){{ :; }}; is_uint(){{ [[ $1 =~ ^[0-9]+$ ]]; }}
now(){{ date +%s; }}
{supervise}
supervise 0 0
'''
        process = subprocess.Popen(["bash", "-c", harness])
        self.processes.append(process)
        time.sleep(1)

        process.terminate()

        self.assertEqual(process.wait(timeout=5), 0)
        self.assertEqual((self.run / "reason").read_text(), "stopped")

    def test_a_lookalike_process_from_another_uid_is_ignored(self):
        # Any local user can start a process whose argv contains `$SELF
        # __supervise` and ignore TERM. supervisor_pids must require the
        # supervisor's real UID (0 in production), or stop waits 10 s for the
        # look-alike, kills it, and logs that a supervisor ignored TERM.
        ready = self.run / "lookalike-ready"
        self.helper.write_text(
            "#!/usr/bin/env bash\n"
            "trap '' TERM\n"
            f"touch {ready}\n"
            "sleep 30\n")
        lookalike = self.spawn("__supervise", "0", "0")
        # The TERM below must reach the look-alike after its trap is in place.
        deadline = time.monotonic() + 5
        while not ready.exists():
            self.assertLess(time.monotonic(), deadline, "look-alike never started")
            time.sleep(0.01)
        supervisor_uid = None if os.getuid() != 0 else 65534

        result = self.run_fragment(
            f"supervisor_pids; kill -TERM {lookalike.pid}; wait_supervisors_gone",
            supervisor_uid=supervisor_uid,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")
        self.assertIsNone(lookalike.poll())


if __name__ == "__main__":
    unittest.main()
