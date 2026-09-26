"""A name lookup reports each answer as it arrives and asks late arrivals.

The panel's radar showed a name only when a whole lookup ended, which is the
wake's 30 s deadline whenever any peer stays silent, and a device that joined
the peer table after a lookup started waited for the next one. These pin the
two fixes against a stubbed network: no radio, no Bluetooth, no subprocesses.
"""
import contextlib
import importlib.machinery
import importlib.util
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_sender():
    path = ROOT / "userspace" / "send-to-peer"
    loader = importlib.machinery.SourceFileLoader("send_to_peer", str(path))
    spec = importlib.util.spec_from_loader("send_to_peer", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class NameLookupTests(unittest.TestCase):
    def setUp(self):
        self.stp = load_sender()
        self.stp.RESCAN_SECONDS = 0.05
        self.release_slow = threading.Event()
        self.addCleanup(self.release_slow.set)

        @contextlib.contextmanager
        def wake():
            yield time.monotonic() + 5
        self.stp.wake = wake

    def fake_peer_name(self, answers, slow=()):
        def peer_name(mac, port, sender_name, ready_by=None):
            if mac in slow:
                self.release_slow.wait(5)
            return answers[mac]
        self.stp.peer_name = peer_name

    def test_an_answer_is_reported_before_a_silent_peer_gives_up(self):
        self.fake_peer_name({"aa": "iMac", "bb": None}, slow={"bb"})
        seen = []

        def on_result(mac, name):
            seen.append((mac, name))
            if mac == "aa":
                # Only once the fast answer is out does the slow peer finish.
                self.release_slow.set()

        names = self.stp.peer_names(["aa", "bb"], 8770, "test", on_result=on_result)
        self.assertEqual(seen, [("aa", "iMac"), ("bb", None)])
        self.assertEqual(names, {"aa": "iMac", "bb": None})

    def test_a_peer_heard_mid_lookup_is_asked_in_the_same_lookup(self):
        self.fake_peer_name({"aa": None, "cc": "Beata's iMac"}, slow={"aa"})
        tables = iter([["aa"], ["aa", "cc"]])

        def rescan():
            table = next(tables, ["aa", "cc"])
            if "cc" in table:
                threading.Timer(0.2, self.release_slow.set).start()
            return table

        names = self.stp.peer_names(["aa"], 8770, "test", rescan=rescan)
        self.assertEqual(names.get("cc"), "Beata's iMac")

    def test_no_newcomers_are_asked_without_a_wake(self):
        self.fake_peer_name({"aa": None, "cc": "late"})
        names = self.stp.peer_names(["aa"], 8770, "test", with_wake=False,
                                    rescan=lambda: ["aa", "cc"])
        self.assertEqual(names, {"aa": None})


if __name__ == "__main__":
    unittest.main()
