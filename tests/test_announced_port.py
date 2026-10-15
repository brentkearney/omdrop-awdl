"""send-to-peer dials the AirDrop port a peer announced, and keeps the wake up
through a send to an Android phone.

Android's Quick Share serves AirDrop on a random port, not 8770, and closes the
receiver about ten seconds after our BLE wake stops. The helper's `peers`
reports the port as a trailing `port=N`; a helper that prints none (this
repository's) leaves every peer on --port. A stubbed network, subprocesses and
Bluetooth: no radio, no phone.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import time
import types
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID = "aa:50:8e:d9:c9:ce"   # announced port 35471, in the Galaxy S24 run that worked
APPLE = "7e:49:49:fe:ea:4e"


def load_sender():
    path = ROOT / "userspace" / "send-to-peer"
    loader = importlib.machinery.SourceFileLoader("send_to_peer", str(path))
    spec = importlib.util.spec_from_loader("send_to_peer", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class AnnouncedPortTests(unittest.TestCase):
    def setUp(self):
        self.stp = load_sender()
        self.events = []
        self.stp.PORTS = {ANDROID: 35471}
        self.stp.peers = lambda: [(ANDROID, None), (APPLE, None)]
        # The module's own clock and subprocess, so nothing leaks into the
        # real modules other tests use.
        self.stp.time = types.SimpleNamespace(
            monotonic=time.monotonic, sleep=lambda s: None,
            time=time.time, strftime=time.strftime, gmtime=time.gmtime)

        @contextlib.contextmanager
        def wake(seconds=30, purpose=""):
            self.events.append(("wake up", seconds))
            try:
                yield time.monotonic() + seconds
            finally:
                self.events.append(("wake down",))
        self.stp.wake = wake

        def run(cmd, **kw):
            self.events.append(("send",))
            return types.SimpleNamespace(returncode=0)
        self.stp.subprocess = types.SimpleNamespace(run=run)

    def main(self, *argv, listening=(True,)):
        answers = iter(listening)

        def knock(host, port, timeout=2.0):
            self.events.append(("knock", port))
            return next(answers, False)
        self.stp.listening = knock
        out = io.StringIO()
        with unittest.mock.patch("sys.argv", ["send-to-peer", *argv]):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = self.stp.main()
        return rc, out.getvalue()

    def names(self):
        return [e[0] for e in self.events]

    def test_the_table_read_takes_ports_from_the_helper_and_drops_them_with_it(self):
        stp = load_sender()
        out = types.SimpleNamespace(
            returncode=0, stdout=f"{ANDROID} rssi=? port=35471\n{APPLE} rssi=?\n", stderr="")
        stp.subprocess = types.SimpleNamespace(run=lambda *a, **k: out)
        stp.registered_peers = lambda: [ANDROID, APPLE]
        stp.own_address = lambda: None
        stp.peers()
        self.assertEqual(stp.PORTS, {ANDROID: 35471})
        out.stdout = f"{ANDROID} rssi=?\n"   # a helper that announces no ports
        stp.peers()
        self.assertEqual(stp.PORTS, {})

    def test_a_send_dials_the_announced_port_and_apple_gets_8770(self):
        _, android = self.main("--mac", ANDROID, "--print", "f")
        _, apple = self.main("--mac", APPLE, "--print", "f")
        self.assertIn("%awdl0]:35471", android)
        self.assertIn("%awdl0]:8770", apple)

    def test_the_listing_shows_each_peers_port(self):
        _, out = self.main("--list")
        endpoint = {line.split()[0]: line.split()[-1] for line in out.splitlines()}
        self.assertTrue(endpoint[ANDROID].endswith("%awdl0]:35471"))
        self.assertTrue(endpoint[APPLE].endswith("%awdl0]:8770"))

    def test_a_knock_follows_a_port_announced_while_it_waits(self):
        def knock(host, port, timeout=2.0):
            self.events.append(("knock", port))
            self.stp.PORTS = {ANDROID: 35471}   # the table is re-read meanwhile
            return port == 35471
        self.stp.PORTS = {}
        self.stp.listening = knock
        self.assertTrue(self.stp._knock(ANDROID, 8770, time.monotonic() + 5))
        self.assertEqual([e[1] for e in self.events], [8770, 35471])

    def test_a_names_listing_wakes_an_empty_table_and_lists_who_joins(self):
        # An S24 already in Quick Share receive mode joins AWDL only after it
        # hears the wake, so the table is empty when the lookup starts.
        tables = iter([[], [], [(ANDROID, None)]])
        self.stp.peers = lambda: next(tables, [(ANDROID, None)])
        self.stp.peer_name = lambda mac, port, name, ready_by=None: "Kerem S24 Ultra"

        @contextlib.contextmanager
        def wake(seconds=30, purpose=""):
            self.events.append(("wake up", seconds))
            yield time.monotonic() + 0.3
            self.events.append(("wake down",))
        self.stp.wake = wake
        self.stp.RESCAN_SECONDS = 0.05
        rc, out = self.main("--list", "-n", "--stream")
        self.assertEqual(rc, 0)
        self.assertEqual(self.names()[0], "wake up")
        self.assertIn(ANDROID, out)
        self.assertIn("Kerem S24 Ultra", out)

    def test_an_empty_table_without_names_still_says_nothing_is_there(self):
        self.stp.peers = lambda: []
        rc, _ = self.main("--list")
        self.assertEqual(rc, 1)
        self.assertNotIn("wake up", self.names())

    def test_android_keeps_the_wake_up_from_before_the_send_until_it_ends(self):
        rc, _ = self.main("--mac", ANDROID, "f", "--wait", "30")   # receiver already open
        self.assertEqual(rc, 0)
        self.assertEqual(self.events, [("wake up", self.stp.SEND_WAKE_SECONDS),
                                       ("knock", 35471), ("send",), ("wake down",)])

    def test_android_gets_one_wake_for_the_wait_and_the_send(self):
        rc, _ = self.main("--mac", ANDROID, "f", "--wait", "30", listening=(False, False, True))
        self.assertEqual(rc, 0)
        self.assertEqual(self.names().count("wake up"), 1)
        self.assertLess(self.names().index("wake up"), self.names().index("knock"))
        self.assertGreater(self.names().index("wake down"), self.names().index("send"))

    def test_android_without_wake_raises_none(self):
        self.assertEqual(self.main("--mac", ANDROID, "f", "--no-wake")[0], 0)
        self.assertNotIn("wake up", self.names())

    def test_an_apple_peer_that_is_listening_gets_no_wake_and_a_waiting_one_loses_it_before_the_send(self):
        self.assertEqual(self.main("--mac", APPLE, "f", "--wait", "30")[0], 0)
        self.assertNotIn("wake up", self.names())
        self.events.clear()
        self.assertEqual(self.main("--mac", APPLE, "f", "--wait", "30", listening=(False, True))[0], 0)
        self.assertEqual(self.events[0], ("knock", 8770))
        self.assertLess(self.names().index("wake down"), self.names().index("send"))


if __name__ == "__main__":
    unittest.main()
