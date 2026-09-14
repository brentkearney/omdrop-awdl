#!/usr/bin/env python3
"""Register AWDL peers the moment their first frame arrives.

Two things have to exist before a Mac can complete a connection to us, and
both are derived from nothing but its MAC:

  1. a firmware peer entry (`awdl_peer_op ADD`) -- without it our unicast to
     the Mac completes tx_status 0x0003 FW_TOSSED (065508Z), so its DISCOVER
     fails and the share sheet says "No People Found";
  2. a permanent neighbour for fe80::<EUI-64 of MAC> -- ND cannot resolve
     over AWDL (062218Z: 513 NS, 0 NA), and the Mac's link-local is always
     the EUI-64 of its AWDL MAC.

The hold loop used to do both on a ~3 s poll keyed on the kernel neighbour
table, which only gets an entry once the Mac sends *unicast*; its first frames
after the sheet opens are multicast mDNS queries, seconds earlier, and every
DISCOVER in the gap failed (213115Z: 1, 215526Z: 6 over ~50 s). This watches
the interface itself and acts on the first frame from each new source MAC.

Run as root while awdl0 is up; SIGTERM ends it. One line per event on stdout.
Use --prime MAC to register one explicitly selected peer without waiting for
its first frame, print its link-local address, and exit. Failure exits nonzero.
"""
import argparse
import os
import socket
import subprocess
import sys
import time

# Helpers are siblings of this file. Resolving them against the working
# directory instead made every peer_op ADD fail with python's "can't open
# file" whenever the caller ran from anywhere but the checkout root -- and a
# peer with no firmware entry silently swallows every unicast we send it.
IOVAR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'brcm_iovar.py')

ETH_P_ALL = 0x0003
PACKET_OUTGOING = 4
# Generic HT Capabilities IE (mode 01, "HT-only" 37-byte form) -- the form the
# firmware accepted in peerop2 and that made unicast complete 0x0000 (074311Z).
HT_IE = bytes.fromhex('2d1a6f881bffff000000000000000096000100000000000000000000')

ap = argparse.ArgumentParser()
ap.add_argument('--iface', default='awdl0')
ap.add_argument('--max-peers', type=int, default=8,
                help='stop adding firmware entries after this many distinct peers')
ap.add_argument('--only', default='',
                help='comma-separated MACs; register these and nothing else (223044Z/231000Z: '
                     'data stalled both ways after transfers in the two runs that registered strangers)')
ap.add_argument('--prime', metavar='MAC',
                help='register this explicitly selected peer and exit, printing its link-local address')
args = ap.parse_args()
ONLY = {bytes.fromhex(m.strip().replace(':', '')) for m in args.only.split(',') if m.strip()}

with open(f'/sys/class/net/{args.iface}/address') as f:
    OURMAC = bytes.fromhex(f.read().strip().replace(':', ''))


def now():
    return time.strftime('%H:%M:%SZ', time.gmtime())


def eui64_ll(mac):
    b = bytearray(mac)
    b[0] ^= 0x02
    return 'fe80::%02x%02x:%02xff:fe%02x:%02x%02x' % (b[0], b[1], b[2], b[3], b[4], b[5])


def register(mac):
    macs = ':'.join(f'{x:02x}' for x in mac)
    t0 = time.monotonic()
    ll = eui64_ll(mac)
    neighbour = subprocess.run(['ip', '-6', 'neigh', 'replace', ll, 'lladdr', macs, 'dev', args.iface,
                                'nud', 'permanent'], check=False, timeout=8)
    if neighbour.returncode:
        print(f'{now()} peer {macs} neighbour registration failed rc={neighbour.returncode}', flush=True)
        return False
    payload = b'\x00\x00' + mac + b'\x01' + HT_IE
    r = subprocess.run([sys.executable, IOVAR, '-i', args.iface, 'set',
                        'awdl_peer_op', payload.hex()], capture_output=True, text=True, timeout=8)
    tail = (r.stdout.strip().splitlines() or [''])[-1].split('-> ')[-1]
    print(f'{now()} peer {macs} neigh {ll} pinned; peer_op ADD -> {tail} rc={r.returncode} '
          f'({(time.monotonic() - t0) * 1000:.0f} ms)', flush=True)
    return r.returncode == 0 and tail == 'OK'


if args.prime:
    try:
        peer = bytes.fromhex(args.prime.replace(':', ''))
    except ValueError:
        ap.error('--prime requires a unicast peer MAC')
    if len(peer) != 6 or peer[0] & 1 or peer == OURMAC or (ONLY and peer not in ONLY):
        ap.error('--prime requires a selected unicast peer other than this interface')
    if not register(peer):
        sys.exit(1)
    print(f'{now()} primed {eui64_ll(peer)}', flush=True)
    sys.exit(0)

s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
s.bind((args.iface, 0))
seen = set()
print(f'{now()} watching {args.iface} for new peers', flush=True)
while True:
    frame, (_, _, pkttype, _, _) = s.recvfrom(2048)
    if pkttype == PACKET_OUTGOING or len(frame) < 12:
        continue
    src = frame[6:12]
    if src in seen or src == OURMAC or src[0] & 1:
        continue
    seen.add(src)
    if ONLY and src not in ONLY:
        print(f'{now()} peer {src.hex(":")} seen; not in --only, skipped', flush=True)
        continue
    if len(seen) > args.max_peers:
        print(f'{now()} peer {src.hex(":")} seen; not adding (max-peers {args.max_peers})', flush=True)
        continue
    register(src)
