#!/usr/bin/env python3
"""Decode the BCM4387 `awdl_stats` iovar (version 3, 324 bytes).

Field offsets come from AppleBCMWLANProximityInterface::dumpAwdlStats
(kernelcache 0xfffffe0008f41e44), which prints the buffer with
    "AWDL stats: afrx %lu aftx %lu datatx %lu datarx %lu txdrop %lu rxdrop %lu
     monrx %lu lostmaster %lu misalign %lu aws %lu aw_dur %lu debug %lu
     txsupr %lu afrxdrop %lu awdrop %lu noawchansw %lu rx80211 %lu
     peeropdrop %lu psfchanswtchskip %lu psfstateupdskip %lu"
reading u32s at +4..+72 step 4 and then +176, +180. That is the router
header's awdl_stats_t order behind a {u16 version, u16 length} header.

Usage:
    awdl-stats.py FILE...          one row per snapshot, deltas between rows
    awdl-stats.py --rx FILE...     receive-side fields only
    awdl-stats.py --all FILE...    every nonzero u32 word by offset, not just
                                   the named ones. Live counters exist at
                                   +104..+272 outside the named list.

CAUTION (results/txcount-20260902T233*Z): `datatx` (+12) stayed 0 across four
sequence variants while 30 frames were submitted in each, and `txdrop` (+20),
`+116`, `+196` all advanced at sequence-dependent BACKGROUND rates (+196 is
~0.95/s with slot 0 = 44 and ~1.9/s with slot 0 = 6, frames or no frames).
None of these words counts our data frames. `aftx` (+8, our PSFs/MIFs) and
`afrx` (+4) are the only fields validated against something observable.
"""
import struct, sys, os

FIELDS = [("afrx", 4), ("aftx", 8), ("datatx", 12), ("datarx", 16),
          ("txdrop", 20), ("rxdrop", 24), ("monrx", 28), ("lostmaster", 32),
          ("misalign", 36), ("aws", 40), ("aw_dur", 44), ("debug", 48),
          ("txsupr", 52), ("afrxdrop", 56), ("awdrop", 60), ("noawchansw", 64),
          ("rx80211", 68), ("peeropdrop", 72),
          ("psfchanswtchskip", 176), ("psfstateupdskip", 180)]
RX = {"afrx", "datarx", "rxdrop", "monrx", "afrxdrop", "awdrop", "rx80211",
      "peeropdrop", "noawchansw", "lostmaster", "misalign"}


def decode(b):
    ver, ln = struct.unpack_from("<HH", b, 0)
    if ver != 3 or ln != 324 or len(b) < 184:
        raise ValueError(f"not awdl_stats v3: ver={ver} len={ln} bytes={len(b)}")
    return {n: struct.unpack_from("<I", b, o)[0] for n, o in FIELDS}


def main(argv):
    rx = "--rx" in argv
    allw = "--all" in argv
    files = [a for a in argv if not a.startswith("--")]
    if allw:
        rows = []
        for f in files:
            b = open(f, "rb").read()
            rows.append((os.path.basename(f),
                         [struct.unpack_from("<I", b, o)[0] for o in range(0, len(b) - 3, 4)]))
        n = min(len(r) for _, r in rows)
        named = {o: nm for nm, o in FIELDS}
        print(f"{'off':>5} {'name':<17}" + "".join(f"{t[:14]:>15}" for t, _ in rows)
              + ("   deltas" if len(rows) > 1 else ""))
        for i in range(1, n):
            vals = [r[i] for _, r in rows]
            if not any(vals):
                continue
            ds = [vals[k + 1] - vals[k] for k in range(len(vals) - 1)]
            print(f"{'+' + str(i * 4):>5} {named.get(i * 4, ''):<17}"
                  + "".join(f"{v:>15d}" for v in vals)
                  + ("   " + ",".join(f"{d:+d}" for d in ds) if ds else ""))
        return
    snaps = []
    for f in files:
        try:
            snaps.append((os.path.basename(f), decode(open(f, "rb").read())))
        except (ValueError, OSError) as e:
            print(f"{f}: {e}", file=sys.stderr)
    if not snaps:
        sys.exit(1)
    names = [n for n, _ in FIELDS if (not rx or n in RX)]
    w = max(len(n) for n in names)
    print(f"{'field':<{w}}" + "".join(f"{t[:14]:>15}" for t, _ in snaps)
          + ("   deltas" if len(snaps) > 1 else ""))
    for n in names:
        vals = [s[n] for _, s in snaps]
        if not any(vals):
            continue
        ds = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
        print(f"{n:<{w}}" + "".join(f"{v:>15d}" for v in vals)
              + ("   " + ",".join(f"{d:+d}" for d in ds) if ds else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
