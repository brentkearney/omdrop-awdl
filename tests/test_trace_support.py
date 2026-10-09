import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISCOVERABLE = ROOT / "userspace" / "omdrop-discoverable"

# The two forms of the AWDL tx-completion line a brcmfmac can carry. Only the
# pr_info() one, with the prefix in its format string, ever reaches the log
# without awdl_trace; the brcmf_dbg() one from aurora-silicon/linux bc1823a897a7
# needs a DEBUG build and the MSGBUF debug bit.
PRINTED = b"brcmfmac: awdl txstatus ring_ifidx=%u\0\n"
DBG_ONLY = b"awdl txstatus ring_ifidx=%u\0\n"


@unittest.skipUnless(shutil.which("zstd"), "needs zstd")
class TraceSupportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def module(self, name, token, compress=True):
        # The token sits near the start of a large file, followed by a newline:
        # the shape that makes an early-exiting grep SIGPIPE the decompressor.
        raw = self.root / name
        raw.write_bytes(token + random.Random(4387).randbytes(4 << 20))
        if not compress:
            return raw
        subprocess.run(["zstd", "-q", "--rm", str(raw), "-o", f"{raw}.zst"], check=True)
        return Path(f"{raw}.zst")

    def trace_support(self, ko, disk_src="A1", loaded_src="A1", trace="/nonexistent"):
        source = DISCOVERABLE.read_text()
        start = source.index("trace_support(){")
        fragment = source[start:source.index("\n}\n", start) + 3]
        harness = f'''
set -uo pipefail
TRACE={trace}
grep(){{ /usr/bin/grep "$@"; }}
modinfo(){{ case $1 in -n) echo {ko};; -F) echo {disk_src};; esac; }}
cat(){{ if [[ $1 == /sys/module/brcmfmac/srcversion ]]; then echo {loaded_src}; else command cat "$@"; fi; }}
{fragment}
trace_support
'''
        result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_printing_module_is_capable_despite_early_match(self):
        self.assertEqual(self.trace_support(self.module("printed.ko", PRINTED)), "yes")

    def test_uncompressed_printing_module_is_capable(self):
        ko = self.module("printed.ko", PRINTED, compress=False)
        self.assertEqual(self.trace_support(ko), "yes")

    def test_dbg_only_module_is_not_capable(self):
        self.assertEqual(self.trace_support(self.module("dbg.ko", DBG_ONLY)), "no")

    def test_parameter_wins_over_module_contents(self):
        ko = self.module("dbg.ko", DBG_ONLY)
        self.assertEqual(self.trace_support(ko, trace=self.root), "yes")

    def test_module_on_disk_that_is_not_loaded_is_unknown(self):
        ko = self.module("dbg.ko", DBG_ONLY)
        self.assertEqual(self.trace_support(ko, disk_src="NEW", loaded_src="OLD"), "unknown")

    def test_failed_decompression_is_unknown(self):
        ko = self.module("printed.ko", PRINTED)
        ko.write_bytes(ko.read_bytes()[:100000])
        self.assertEqual(self.trace_support(ko), "unknown")

    def test_missing_module_file_is_unknown(self):
        self.assertEqual(self.trace_support(self.root / "missing.ko.zst"), "unknown")


if __name__ == "__main__":
    unittest.main()
