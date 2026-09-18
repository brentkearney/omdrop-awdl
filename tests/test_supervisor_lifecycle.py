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

    def run_fragment(self, body):
        harness = f'''\nset -uo pipefail\nRUN={self.run}\nSELF={self.helper}\n# The workstation's grep replacement cannot search /proc/*/cmdline. The\n# lifecycle must not depend on grep accepting binary procfs files.\ngrep(){{ return 1; }}\n{self.lifecycle_fragment()}\n{body}\n'''
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


if __name__ == "__main__":
    unittest.main()
