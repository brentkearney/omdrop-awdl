#!/usr/bin/env python3
"""Decode the awdl_advertisers reply as a table of awdl_peer_node_t.

The reply overwrites the request buffer, so bytes 0-3 are leftovers from the
iovar name except a u16 total-length at offset 2. Packed 60-byte peer nodes
follow from offset 4. Field offsets are awdl_peer_node_t from wlioctl, and they
line up exactly: period_tu=110, aw_period/aw_cmn_length/aw_ext_length=16,
self_metrics=510 -- all sane AWDL values in the right places, which is what
distinguishes this from a coincidence.
"""
import struct, sys

NODE = 60

def decode(b):
    total = struct.unpack_from('<H', b, 2)[0]
    out = []
    for off in range(4, min(4 + total, len(b)) - 40, NODE):
        (type_state, aw_counter, rssi, last_rssi, tx_counter, tx_delay,
         period_tu, aw_period, aw_cmn, aw_ext, self_m, top_m) = \
            struct.unpack_from('<IHbbHHHHHHII', b, off)
        addr = b[off + 28:off + 34]
        top = b[off + 34:off + 40]
        dist = b[off + 40]
        out.append(dict(addr=':'.join(f'{x:02x}' for x in addr),
                        top_master=':'.join(f'{x:02x}' for x in top),
                        rssi=rssi, last_rssi=last_rssi, aw_counter=aw_counter,
                        period_tu=period_tu, aw_period=aw_period,
                        aw_cmn=aw_cmn, aw_ext=aw_ext, self_metric=self_m,
                        top_metric=top_m, dist_top=dist,
                        tx_counter=tx_counter, tx_delay=tx_delay))
    return total, out

for p in sys.argv[1:]:
    b = open(p, 'rb').read()
    total, peers = decode(b)
    print(f'{p.split("/")[-1]:<14} table_len={total:<4} peers={len(peers)}')
    for pr in peers:
        print(f'    {pr["addr"]}  rssi={pr["rssi"]:>4}/{pr["last_rssi"]:<4} '
              f'aw_counter={pr["aw_counter"]:<6} af_period={pr["period_tu"]:<4} '
              f'aw={pr["aw_period"]}/{pr["aw_cmn"]}/{pr["aw_ext"]:<3} '
              f'metric={pr["self_metric"]:<5} top={pr["top_master"]} '
              f'top_metric={pr["top_metric"]:<5} dist={pr["dist_top"]}')
