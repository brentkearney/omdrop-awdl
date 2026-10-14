"""send-to-peer names the sender after this machine unless told otherwise."""
import argparse
import importlib.machinery
import importlib.util
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
    def test_default_name_is_the_short_hostname(self):
        stp = load_sender()
        seen = {}

        def capture(parser, *args, **kwargs):
            seen["name"] = parser.get_default("name")
            raise SystemExit(0)

        with unittest.mock.patch.object(stp.socket, "gethostname", return_value="Reviades.local"), \
                unittest.mock.patch.object(argparse.ArgumentParser, "parse_args", capture), \
                self.assertRaises(SystemExit):
            stp.main()
        self.assertEqual(seen["name"], "Reviades")


if __name__ == "__main__":
    unittest.main()
