#!/usr/bin/env python3
"""Decode awdl_election_tree_info_t -- OUR OWN election state.

Layout from wlioctl. The first 15 bytes are writable; everything from top_master
on is marked read-only, i.e. computed by the firmware's election state machine.

Why this matters: the peers' advertised top_master is readable from
awdl_advertisers, so if our metric exceeds theirs and they hear us, their
top_master should flip to our MAC. That turns "do they hear us" into a read,
with no capture and no second machine.
"""
import struct, sys

def decode(b):
    (flags, eid, self_m, close_sync, boost, edge_sync, close_rng, mid_rng,
     mhm_close, mhm_mid, max_depth) = struct.unpack_from('<BHIbbbbbBBB', b, 0)
    top = ':'.join(f'{x:02x}' for x in b[15:21])
    top_metric, = struct.unpack_from('<I', b, 21)
    depth = b[25]
    return dict(flags=flags, election_id=eid, self_metric=self_m,
                close_sync_rssi=close_sync, master_rssi_boost=boost,
                edge_sync_rssi=edge_sync, close_range_rssi=close_rng,
                mid_range_rssi=mid_rng, max_higher_close=mhm_close,
                max_higher_mid=mhm_mid, max_tree_depth=max_depth,
                top_master=top, top_master_metric=top_metric,
                current_tree_depth=depth)

for p in sys.argv[1:]:
    d = decode(open(p, 'rb').read())
    print(f'  {p.split("/")[-1]}:')
    print(f'    self_metric      = {d["self_metric"]}')
    print(f'    election_id      = {d["election_id"]}   flags = {d["flags"]}')
    print(f'    top_master       = {d["top_master"]}  metric {d["top_master_metric"]}')
    print(f'    tree_depth       = {d["current_tree_depth"]}  max {d["max_tree_depth"]}')
    print(f'    rssi thresholds  = close_sync {d["close_sync_rssi"]} '
          f'boost {d["master_rssi_boost"]} edge_sync {d["edge_sync_rssi"]} '
          f'close_range {d["close_range_rssi"]} mid_range {d["mid_range_rssi"]}')
    print(f'    max_higher_masters close={d["max_higher_close"]} mid={d["max_higher_mid"]}')
