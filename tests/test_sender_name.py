"""send-to-peer names the sender after this machine unless told otherwise."""
import contextlib
import importlib.machinery
import importlib.util
import io
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_sender():
    path = ROOT / "userspace" / "send-to-peer"
    loader = importlib.machinery.SourceFileLoader("send_to_peer", str(path))
    spec = importlib.util.spec_from_loader("send_to_peer", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class SenderNameTests(unittest.TestCase):
    def printed_command(self, *options):
        stp = load_sender()
        output = io.StringIO()
        with unittest.mock.patch.object(stp.socket, "gethostname", return_value="Reviades.local"), \
                unittest.mock.patch.object(stp, "peers", return_value=[("02:11:22:33:44:55", -40)]), \
                unittest.mock.patch.object(sys, "argv", ["send-to-peer", "--print", *options, "fixture.txt"]), \
                contextlib.redirect_stdout(output):
            self.assertEqual(stp.main(), 0)
        return output.getvalue().strip()

    def test_default_name_is_the_short_hostname(self):
        self.assertTrue(
            self.printed_command().endswith("--name Reviades -- fixture.txt"))

    def test_explicit_name_is_preserved(self):
        self.assertTrue(
            self.printed_command("--name", "My Laptop.local").endswith(
                "--name My Laptop.local -- fixture.txt"))


if __name__ == "__main__":
    unittest.main()
