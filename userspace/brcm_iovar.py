#!/usr/bin/env python3
"""Probe brcmfmac firmware iovars from userspace, with no kernel patch.

Transport: nl80211 NL80211_CMD_VENDOR carrying Broadcom's dcmd passthrough
(BROADCOM_OUI 0x001018, subcmd BRCMF_VNDR_CMDS_DCMD=1). brcmfmac compiles
vendor.o in unconditionally, so this works against the stock Asahi kernel.

Wire format, from brcmfmac/vendor.{c,h}:

    struct brcmf_vndr_dcmd_hdr {
            uint cmd;      /* dongle dcmd id */
            int  len;      /* size of the expected return buffer */
            uint offset;   /* offset of the payload (== sizeof hdr) */
            uint set;      /* 0 = get, 1 = set */
            uint magic;    /* unused by the handler */
    };

followed by the dcmd payload. An iovar is dcmd BRCMF_C_GET_VAR (262) or
BRCMF_C_SET_VAR (263) whose payload is "name\\0" + data (brcmf_create_iovar).

CAVEAT that shapes every result here: brcmf_fil_cmd_data() collapses *every*
firmware error to -EBADE unless ifp->fwil_fwerr is set, which only feature.c
does, transiently. So we cannot read BCME_UNSUPPORTED vs BCME_BADARG. The
discriminator used instead is success-at-any-length across a buffer sweep,
calibrated against known-present and known-absent control iovars. See
probe_iovar().

Requires CAP_NET_ADMIN.
"""

import argparse
import errno
import os
import socket
import string
import time
import struct
import sys

# --- netlink ---------------------------------------------------------------
NETLINK_GENERIC = 16
NLMSG_ERROR, NLMSG_DONE = 2, 3
NLM_F_REQUEST, NLM_F_ACK = 0x01, 0x04

GENL_ID_CTRL = 16
CTRL_CMD_GETFAMILY = 3
CTRL_ATTR_FAMILY_ID, CTRL_ATTR_FAMILY_NAME = 1, 2

# --- nl80211 (from /usr/include/linux/nl80211.h on this machine) -----------
NL80211_CMD_VENDOR = 103
NL80211_ATTR_IFINDEX = 3
NL80211_ATTR_VENDOR_ID = 195
NL80211_ATTR_VENDOR_SUBCMD = 196
NL80211_ATTR_VENDOR_DATA = 197

# --- brcmfmac --------------------------------------------------------------
BROADCOM_OUI = 0x001018
BRCMF_VNDR_CMDS_DCMD = 1
BRCMF_VNDR_CMDS_AWDL = 2
AWDL_OP_CREATE, AWDL_OP_DESTROY, AWDL_OP_FWDUMP = 0, 1, 2
BRCMF_NLATTR_LEN, BRCMF_NLATTR_DATA = 1, 2
BRCMF_C_GET_VAR, BRCMF_C_SET_VAR = 262, 263
BRCMF_DCMD_MAXLEN = 8192
DCMD_HDR_LEN = 20


def _attr(atype, payload):
    alen = 4 + len(payload)
    return struct.pack("=HH", alen, atype) + payload + b"\0" * (-alen % 4)


def _parse_attrs(buf):
    out, off = {}, 0
    while off + 4 <= len(buf):
        alen, atype = struct.unpack_from("=HH", buf, off)
        if alen < 4:
            break
        out[atype & 0x3FFF] = buf[off + 4: off + alen]
        off += (alen + 3) & ~3
    return out


class Netlink:
    def __init__(self, timeout=5.0):
        self.sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW,
                                  NETLINK_GENERIC)
        self.sock.bind((0, 0))
        self.sock.settimeout(timeout)
        self.seq = 0
        self.family_id = self._resolve_family(b"nl80211")

    def _next_seq(self):
        self.seq += 1
        return self.seq

    def _send(self, family_id, cmd, version, attrs):
        seq = self._next_seq()
        body = struct.pack("=BBH", cmd, version, 0) + attrs
        hdr = struct.pack("=IHHII", 16 + len(body), family_id,
                          NLM_F_REQUEST | NLM_F_ACK, seq, 0)
        self.sock.send(hdr + body)
        return seq

    def _recv(self, family_id, seq):
        """Collect vendor DATA chunks until the ACK/error for `seq` arrives.

        Messages carrying any other sequence number are skipped. Without that
        check a stale ACK left in the receive buffer by an earlier request is
        read as a zero-error success, which silently turns an absent iovar
        into a PRESENT one.
        """
        chunks, err = [], 0
        while True:
            try:
                buf = self.sock.recv(65536)
            except socket.timeout:
                raise RuntimeError("netlink timeout")
            off, done = 0, False
            while off + 16 <= len(buf):
                mlen, mtype, _flags, mseq, _pid = struct.unpack_from("=IHHII",
                                                                    buf, off)
                if mlen < 16:
                    raise RuntimeError("malformed nlmsghdr")
                body = buf[off + 16: off + mlen]
                if mseq != seq:
                    off += (mlen + 3) & ~3
                    continue
                if mtype == NLMSG_ERROR:
                    err = -struct.unpack_from("=i", body, 0)[0]
                    done = True
                elif mtype == NLMSG_DONE:
                    done = True
                elif mtype == family_id:
                    vd = _parse_attrs(body[4:]).get(NL80211_ATTR_VENDOR_DATA)
                    if vd is not None:
                        d = _parse_attrs(vd).get(BRCMF_NLATTR_DATA)
                        if d is not None:
                            chunks.append(d)
                off += (mlen + 3) & ~3
            if done:
                return err, b"".join(chunks)

    def _resolve_family(self, name):
        seq = self._send(GENL_ID_CTRL, CTRL_CMD_GETFAMILY, 1,
                         _attr(CTRL_ATTR_FAMILY_NAME, name + b"\0"))
        # Must drain our own ACK here, or the next request reads it as its own.
        fid = None
        while True:
            buf = self.sock.recv(65536)
            off, done = 0, False
            while off + 16 <= len(buf):
                mlen, mtype, _f, mseq, _p = struct.unpack_from("=IHHII", buf, off)
                body = buf[off + 16: off + mlen]
                if mseq == seq:
                    if mtype == NLMSG_ERROR:
                        e = -struct.unpack_from("=i", body, 0)[0]
                        if e:
                            raise RuntimeError("GETFAMILY failed: %s"
                                               % os.strerror(e))
                        done = True
                    elif mtype == GENL_ID_CTRL:
                        a = _parse_attrs(body[4:]).get(CTRL_ATTR_FAMILY_ID)
                        if a:
                            fid = struct.unpack("=H", a)[0]
                off += (mlen + 3) & ~3
            if done:
                if fid is None:
                    raise RuntimeError("nl80211 family not found")
                return fid


class Brcm:
    def __init__(self, ifname):
        self.ifname = ifname
        self.ifindex = socket.if_nametoindex(ifname)
        self.nl = Netlink()

    def dcmd(self, cmd, payload, ret_len, is_set):
        """Raw dcmd. Returns (err, data); err is a positive errno, 0 on success."""
        if ret_len > BRCMF_DCMD_MAXLEN:
            raise ValueError("ret_len exceeds BRCMF_DCMD_MAXLEN")
        blob = struct.pack("=IiIII", cmd, ret_len, DCMD_HDR_LEN,
                           1 if is_set else 0, 0) + payload
        attrs = (_attr(NL80211_ATTR_IFINDEX, struct.pack("=I", self.ifindex)) +
                 _attr(NL80211_ATTR_VENDOR_ID, struct.pack("=I", BROADCOM_OUI)) +
                 _attr(NL80211_ATTR_VENDOR_SUBCMD,
                       struct.pack("=I", BRCMF_VNDR_CMDS_DCMD)) +
                 _attr(NL80211_ATTR_VENDOR_DATA, blob))
        seq = self.nl._send(self.nl.family_id, NL80211_CMD_VENDOR, 0, attrs)
        return self.nl._recv(self.nl.family_id, seq)

    def awdl(self, op):
        """Invoke the AWDL vendor subcommand added by the netdev-attach patch."""
        attrs = (_attr(NL80211_ATTR_IFINDEX, struct.pack("=I", self.ifindex)) +
                 _attr(NL80211_ATTR_VENDOR_ID, struct.pack("=I", BROADCOM_OUI)) +
                 _attr(NL80211_ATTR_VENDOR_SUBCMD,
                       struct.pack("=I", BRCMF_VNDR_CMDS_AWDL)) +
                 _attr(NL80211_ATTR_VENDOR_DATA, struct.pack("=I", op)))
        seq = self.nl._send(self.nl.family_id, NL80211_CMD_VENDOR, 0, attrs)
        return self.nl._recv(self.nl.family_id, seq)

    def iovar_get(self, name, ret_len=512, data=b""):
        payload = name.encode() + b"\0" + data
        # The handler passes ret_len as the length to firmware, so a ret_len
        # shorter than the name would truncate it before the firmware ever
        # sees which iovar we mean.
        ret_len = max(ret_len, len(payload))
        return self.dcmd(BRCMF_C_GET_VAR, payload, ret_len, False)

    def iovar_set(self, name, data=b""):
        payload = name.encode() + b"\0" + data
        return self.dcmd(BRCMF_C_SET_VAR, payload, len(payload), True)


# --- probing ---------------------------------------------------------------
SWEEP = [4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096]


def _residue(name, n, data_in=b""):
    """The buffer contents the firmware was handed, i.e. what a write-nothing
    reply looks like: "name\0" + data, zero-padded/truncated to n bytes."""
    req = name.encode() + b"\0" + data_in
    return (req + b"\0" * n)[:n]


def _novel(name, n, reply, data_in=b""):
    """(bytes firmware actually changed, total)."""
    res = _residue(name, n, data_in)
    k = min(len(res), len(reply))
    return sum(1 for i in range(k) if res[i] != reply[i]) + abs(len(reply) - k), len(reply)


def probe_iovar(dev, name, sweep=SWEEP):
    """Sweep buffer lengths. Any success => the iovar exists.

    Works around the -EBADE collapse: a firmware that does not know an iovar
    fails at every length, whereas one that does usually succeeds at or above
    its natural size. All-fail is therefore 'absent or never-satisfied', not
    a clean 'absent' -- which is why the control iovars below matter.
    """
    results, best = {}, None
    for n in sweep:
        try:
            err, data = dev.iovar_get(name, ret_len=n)
        except RuntimeError as exc:
            results[n] = ("EXC", str(exc), b"")
            continue
        if err == 0 and len(data) != n:
            tag = "ANOMALY(got %d want %d)" % (len(data), n)
            results[n] = (tag, err, data)
            continue
        nov, tot = _novel(name, n, data)
        tag = "OK" if err == 0 else errno.errorcode.get(err, str(err))
        results[n] = (tag, err, data, nov, tot)
        if err == 0 and best is None:
            best = (n, data, nov, tot)
    return {"name": name, "exists": best is not None,
            "first_ok": best[0] if best else None,
            "data": best[1] if best else b"",
            "novel": best[2] if best else 0,
            "total": best[3] if best else 0,
            "sweep": results}


def hexdump(b, limit=128):
    b = b[:limit]
    out = []
    for i in range(0, len(b), 16):
        row = b[i:i + 16]
        hexs = " ".join("%02x" % c for c in row)
        text = "".join(chr(c) if chr(c) in string.printable[:95] else "."
                       for c in row)
        out.append("    %04x  %-47s  %s" % (i, hexs, text))
    return "\n".join(out)


# Controls calibrate the discriminator: KNOWN_PRESENT must come back OK,
# KNOWN_ABSENT must fail at every length. If either misbehaves, the whole
# probe run is uninterpretable and says so.
KNOWN_PRESENT = ["ver", "cap", "cur_etheraddr"]
KNOWN_ABSENT = ["zzz_no_such_iovar_qq", "definitely_not_an_iovar_42"]

# Discovery sweep: the four known-good AWDL iovars need >=8, >=16, >=16 and
# >=512 bytes respectively, so these six lengths catch anything shaped like
# them without paying for the full 11-length sweep on every candidate.
DISCOVERY_SWEEP = [8, 16, 64, 256, 1024, 4096]

_SUFFIXES = [
    # confirmed present, kept as in-run positive controls
    "opmode", "stats", "psf_dwell",
    # availability windows
    "aw", "aws", "aw_period", "aw_ext", "aw_ext_len", "aw_ext_count",
    "aw_common_len", "aw_counter", "aw_params", "aw_len",
    # action / sync frames
    "af", "af_period", "af_tx", "aftx", "action_frame", "psf", "psf_interval",
    "mif", "mif_dwell",
    # synchronisation and master election
    "sync", "sync_state", "syncstate", "sync_params", "sync_offset",
    "master", "master_chan", "election", "election_params",
    "election_metric", "election_state", "tsf", "tsf_offset", "guard_time",
    # channel sequence
    "chanseq", "chan_seq", "channel_seq", "chanseq_len", "chan", "channel",
    # peers
    "peer", "peers", "peer_op", "peer_add", "peer_del", "peer_list",
    "peer_stats", "peer_table", "peer_cnt", "rssi", "rssi_thresh",
    # interface / lifecycle
    "if", "ifname", "iface", "bsscfg", "mode", "enable", "en", "role",
    "state", "status", "cfg", "config", "params", "param", "init", "down",
    # counters and introspection
    "counters", "cnt", "dump", "ver", "version", "info", "debug", "log",
    # power / presence
    "extcount", "ext_count", "presence", "presence_mode", "pw_opt",
    "pw_mode", "powersave", "min_ext", "max_ext",
    # misc protocol surface
    "bcast", "bcast_params", "mcast", "gas", "oob", "oob_af", "oob_req",
    "uct", "key", "seckey", "ssid", "service", "payload", "ie",
    "data", "datapath", "flow", "prio", "scan", "discovery", "disc",
    "publish", "subscribe", "interval", "period", "dwell", "slot",
]

AWDL_CANDIDATES = (
    ["awdl", "awdl_doiovar_patch"]
    + ["awdl_" + x for x in _SUFFIXES]
    + ["awdl" + x for x in ("if", "mode", "peer", "stats", "sync", "chan")]
    # NAN is AWDL's standardised sibling and Asahi already defines
    # BRCMF_INTERFACE_TYPE_NAN = 3 (interface_create.c:56).
    + ["nan", "nan_enable", "nan_cfg", "nan_iface", "nan_init", "nan_state",
       "nan_ver", "nan_oper_state", "nan_disc", "nan_peer"]
)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-i", "--ifname", default="wld0")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    g = sub.add_parser("get"); g.add_argument("name"); g.add_argument("-l", "--len", type=int, default=512)
    g.add_argument("--raw", metavar="FILE",
                   help="write the reply verbatim to FILE, trailing zeros included. "
                        "The hexdump strips them, which silently turns a 324-byte "
                        "counters blob into a 4-byte one; offline diffing needs the bytes.")
    g.add_argument("-d", "--data", default="",
                   help="hex payload sent with the request; 'dump' takes a subsystem name")
    st = sub.add_parser("set"); st.add_argument("name")
    st.add_argument("hexdata", nargs="?", default="",
                    help="payload as hex, e.g. 01000000 for le32(1)")
    sub.add_parser("cap")
    rc = sub.add_parser("cmd")            # raw WLC command, e.g. WLC_UP=2
    rc.add_argument("num", type=lambda v: int(v, 0))
    rc.add_argument("data", nargs="?", default="")
    rc.add_argument("-l", "--len", type=int, default=8)
    rc.add_argument("--get", action="store_true")
    aw = sub.add_parser("awdl-if"); aw.add_argument("op", choices=["create", "destroy", "fwdump"])
    p = sub.add_parser("probe"); p.add_argument("names", nargs="*")
    args = ap.parse_args()

    if args.cmd == "selftest":
        # Unprivileged: proves the netlink plumbing without touching the device.
        nl = Netlink()
        print("nl80211 family id : %d" % nl.family_id)
        print("ifindex(%s)%s: %d" % (args.ifname, " " * max(0, 8 - len(args.ifname)),
                                     socket.if_nametoindex(args.ifname)))
        print("uid               : %d%s" % (os.getuid(),
              "" if os.getuid() == 0 else "  (probes need root)"))
        return 0

    dev = Brcm(args.ifname)

    if args.cmd == "cmd":
        payload = bytes.fromhex(args.data) if args.data else b""
        is_set = not args.get
        err, reply = dev.dcmd(args.num, payload,
                              args.len if args.get else max(len(payload), 1),
                              is_set)
        tag = "get" if args.get else "set"
        print("cmd %d %s -> %s" % (args.num, tag,
              "OK" if err == 0 else errno.errorcode.get(err, err)))
        if err == 0 and args.get and reply:
            print("    " + reply[:args.len].hex(" "))
        return 0 if err == 0 else 1

    if args.cmd == "awdl-if":
        op = {"create": AWDL_OP_CREATE, "destroy": AWDL_OP_DESTROY,
              "fwdump": AWDL_OP_FWDUMP}[args.op]
        err, _ = dev.awdl(op)
        print("awdl-if %s -> %s" % (args.op, "OK" if err == 0
                                    else errno.errorcode.get(err, err)))
        if err != 0:
            return 1
        # Creation is asynchronous: the vendor command only asks firmware for
        # the interface. The netdev is registered later by the fweh event
        # worker, which is the only context that may call register_netdev()
        # without deadlocking on the wiphy mutex. Wait for it to appear.
        if args.op == "create":
            path = "/sys/class/net/awdl0"
            for _ in range(100):          # 10s
                if os.path.exists(path):
                    print("awdl0 registered after %.1fs" % (_ * 0.1))
                    return 0
                time.sleep(0.1)
            print("awdl0 did not appear within 10s")
            return 1
        return 0

    if args.cmd == "get":
        req = bytes.fromhex(args.data) if args.data else b""
        err, data = dev.iovar_get(args.name, args.len, req)
        if err:
            print("%s: %s" % (args.name, errno.errorcode.get(err, err)))
            return 1
        nov, tot = _novel(args.name, args.len, data, req)
        print("%s: OK len=%d novel=%d/%d" % (args.name, len(data), nov, tot))
        if args.raw:
            with open(args.raw, "wb") as f:
                f.write(data)
        print(hexdump(data.rstrip(b"\0")) or "    (all zero)")
        return 0

    if args.cmd == "set":
        payload = bytes.fromhex(args.hexdata) if args.hexdata else b""
        err, _ = dev.iovar_set(args.name, payload)
        print("set %s = %s -> %s" % (args.name, args.hexdata or "(empty)",
                                     "OK" if err == 0
                                     else errno.errorcode.get(err, err)))
        return 0 if err == 0 else 1

    if args.cmd == "cap":
        err, data = dev.iovar_get("cap", 768)
        if err:
            print("cap failed: %s" % errno.errorcode.get(err, err), file=sys.stderr)
            return 1
        caps = data.split(b"\0")[0].decode(errors="replace")
        toks = sorted(caps.split())
        print("# firmware 'cap' iovar, %d tokens\n" % len(toks))
        for t in toks:
            print(t)
        return 0

    if args.cmd == "probe":
        names = args.names or (KNOWN_PRESENT + KNOWN_ABSENT + AWDL_CANDIDATES)
        print("# pass 1: discovery sweep over %d names" % len(names))
        hits = []
        for name in names:
            if probe_iovar(dev, name, sweep=DISCOVERY_SWEEP)["exists"]:
                hits.append(name)
        print("# pass 1 found %d responders: %s\n" % (len(hits), " ".join(hits)))
        print("# pass 2: full sweep on responders + controls")

        present, absent, echo_only = [], [], []
        for name in sorted(set(hits) | set(KNOWN_PRESENT) | set(KNOWN_ABSENT)):
            r = probe_iovar(dev, name)
            mark = "PRESENT" if r["exists"] else "absent "
            extra = (" first_ok=%-5d novel=%d/%d%s"
                     % (r["first_ok"], r["novel"], r["total"],
                        "  <-- ECHO ONLY, firmware wrote nothing"
                        if r["novel"] == 0 else "")
                     if r["exists"] else
                     "  " + ",".join(sorted({t for t, _, _, _, _ in r["sweep"].values()})))
            print("%-24s %s%s" % (name, mark, extra))
            if r["exists"]:
                (present if r["novel"] else echo_only).append(r)
                body = r["data"].rstrip(b"\0")
                if body:
                    print(hexdump(body))
            else:
                absent.append(r)

        ctl_p = [n for n in KNOWN_PRESENT if n in names]
        ctl_a = [n for n in KNOWN_ABSENT if n in names]
        ok_p = {r["name"] for r in present} | {r["name"] for r in echo_only}
        bad = ([n for n in ctl_p if n not in ok_p] +
               [n for n in ctl_a if n in ok_p])
        print("\n%d real, %d echo-only, %d absent"
              % (len(present), len(echo_only), len(absent)))
        if bad:
            print("CONTROLS FAILED (%s) -- results are not interpretable"
                  % ", ".join(bad))
            return 2
        if ctl_p and ctl_a:
            print("controls OK: known-present succeeded, known-absent failed")
        return 0


if __name__ == "__main__":
    sys.exit(main())
