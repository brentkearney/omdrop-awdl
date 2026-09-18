# TODO

Known gaps in `omdrop-awdl`, with what has already been tested, with supporting information.

The instrumentation patches (0009–0011) are how most of this was found, and their output is the evidence behind nearly everything below.

Each section separates what was observed from what is still unknown. Where a finding rests on a single trial, it says so.

1. [Receive with the Wi-Fi link on 5 GHz](#1-receive-with-the-wi-fi-link-on-5-ghz)
2. [Sending has never completed over AWDL](#2-sending-has-never-completed-over-awdl)
3. [The regulatory domain is not reapplied to a fresh wiphy](#3-the-regulatory-domain-is-not-reapplied-to-a-fresh-wiphy)
4. [`awdl0` loses `IFF_UP` across an `awdl=0/1` cycle](#4-awdl0-loses-iff_up-across-an-awdl01-cycle)
5. [`awdl=0` with a PSF template loaded wedges the firmware](#5-awdl0-with-a-psf-template-loaded-wedges-the-firmware)
6. [The instrumentation logs unconditionally](#6-the-instrumentation-logs-unconditionally)
7. [The kernel tag is pinned](#7-the-kernel-tag-is-pinned)
8. [Only BCM4387 has been tested](#8-only-bcm4387-has-been-tested)
9. [Power cost is unmeasured](#9-power-cost-is-unmeasured)
10. [An active receive window cannot be rescheduled](#10-an-active-receive-window-cannot-be-rescheduled)

## 1. Receive with the Wi-Fi link on 5 GHz

#### What we know

- Receive is proven end to end with the infrastructure link on both 2.4 GHz and 5 GHz.
- **The controlled 5 GHz receive test succeeded.** With `wld0` associated on 5 GHz channel 44, a MacBook Pro peer associated on 6 GHz, and that peer's AWDL master on channel 6 with a 6/149 sequence, the Mac discovered this machine and sent a 61,812-byte JPEG. `/Discover`, `/Ask`, and `/Upload` all returned 200, and the receiver stored the file at its expected size.
- That test separates the prior collision hypothesis: our infrastructure channel 44 was neither the peer's AWDL master channel nor its primary sequence channel. It also ran immediately after a module reload, so the fresh wiphy's world regulatory domain did not prevent 5 GHz association or AWDL receiving.
- The earlier channel-157 failure is an isolated result, not evidence that Omdrop must force the infrastructure link to 2.4 GHz. Three companion attempts were already void because `awdl0` was administratively down.
- A firmer result, from a deliberate A/B: moving the dwell to 2.4-dominant — thirteen of sixteen slots on channel 6 — broke discovery outright, even though that sequence still overlapped the peer on both 149 slots and on the shared channel 6 master slot. Overlap on secondary slots plus the master availability window was not sufficient; the peer's primary social channel had to be in our dwell.
- A 2.4-dominant dwell also degrades synchronization, which gives that result a mechanism: `lostmaster` went from 0.13/s to 0.85/s, because the master's own sync frames ride the 5 GHz slots. Dwelling on 6 costs the sync we need to stay in the peer's tree.
- **The infrastructure band is a sync-quality factor, not the discovery gate.** With the station on 5 GHz ch44 and the AWDL master window on 6, pinning the station to 2.4 GHz roughly halved `lostmaster` (63 → 35 per 30 s) and changed nothing else measurable. It makes the schedule easier to hold; it is not what decides whether a peer finds us.
- **The channel-sequence encoding is load-bearing for 5 GHz.** `awdl_chan_seq` encoding 0 — one channel byte per slot — is accepted by the firmware, and the firmware then schedules **no 5 GHz TX at all** from it. Apple advertises encodings 1 and 3; we use 3 (`{chan, opclass}`, opclass 81 for 2.4 GHz and 128 for 5 GHz). A 5 GHz sequence written in encoding 0 is a silent no-op that reads back correctly.
- **Zero slots are load-bearing too.** A full 16-of-16 sequence drops an idle Mac's replies to zero after about three minutes, and filling every slot with the master's channel destroyed reception outright. The firmware sends our multicast only inside our own non-zero slots and drops — not queues — whatever arrives outside them: about 25% transmitted with a sparse 4-of-16 sequence, 55% with the Mac's dense shape, 99.9% with a full one.
- **A measurement trap that has voided conclusions before:** `awdl0` transmits at `chanspec 0xe09b` = 149/80 MHz, and the Macs' 5 GHz slots are `44++`/80 MHz. A 20 MHz monitor capture can never see 5 GHz AWDL data from any device, so several readings of "no 5 GHz data on air" were the sniffer's bandwidth rather than the air.
- **A 5 GHz infrastructure association does not pin the radio to the infrastructure channel.** Sampling `iw dev awdl0 info` every 20 ms for 10 seconds produced the same dwell distribution with `wld0` on 2.4 GHz channel 1 and on 5 GHz channel 44: channel 44 was 56.2% in both runs, channel 149 was 37.3% versus 38.2%, channel 6 was 6.5% versus 5.6%, and both runs made 55 channel transitions. The advertised 44/149/6 schedule is real under both infrastructure bands.
- **The firmware peer table can be read back** `awdl_peer_op` answers a GET with its peer entries, each carrying that peer's own channel sequence, and those rows have matched a Mac's own `wdutil` report independently. (`awdl_peer_table`, `awdl_peer_stats` and `awdl_peers` are unsupported on this firmware.)


## 2. Sending has never completed over AWDL

#### What we know

- No file has ever reached an Apple device from here. Receiving is the only direction proven end to end. More of the send path works than that sentence suggests, and the phone and the Mac fail in different places.
- **`/Discover` has completed against an iPhone.** 200 with the phone's own name in `ReceiverComputerName`, in 1.13 s, and again at 1.2 s and 12.8 s on later attempts. The endpoint was resolved with no mDNS at all — read out of the phone's MIF Service Response TLVs through the driver's action-frame events, EUI-64 link-local, SRV port 8770 — and TLS was accepted. Resolve, TCP, TLS and the first HTTPS exchange all work in this direction.
- **`/Ask` reaches the phone.** It has been sent and delivered; iOS holds the HTTP response until the user taps, and it went unanswered at 120 s and 300 s with nobody at the phone. A later session found the phone displays no prompt for our `/Ask` in any shape tried, including one rebuilt from a Mac's captured Ask body. What is unproven there is sender identity, not transport.
- **Contacts Only rejects us at TLS.** The phone answers our self-signed sender certificate with `certificate unknown` and its advert then carries an empty TXT. Everyone mode accepts the same certificate.
- **A Mac is a different problem.** Every zero-SYN-ACK run was against a Mac at port 8770 — in the best of them nine SYNs over 20 s, each a retransmission of the same `Seq=0`. A Mac also publishes no `_airdrop._tcp` advert until something wakes it over BLE, and a Mac sitting with its Finder AirDrop window open publishes none either, so there is often nothing to connect to.
- **Our unicast transmit needs a firmware peer entry.** Without `awdl_peer_op ADD` for the destination every frame completes `tx_status 0x0003` = `FW_TOSSED`, discarded before any air attempt; with the entry the fates become `0x0000`, acked by the receiver. This was proven in the receive direction and is the first thing to check in any send failure.
- **Timing matters.** A Mac that has just seen us tear down and return with the same MAC takes about 40 s to talk to us again, so a send fired at T0 races it — fire at roughly T0+30. After a failed send, one Mac's frames *to* us paused for 12 minutes while its reception of us stayed alive (n=1).
- The sender itself is not the problem. Run against a local receiver over loopback it completes the whole exchange — `/Ask` answered, `/Upload` accepted, file stored — so the HTTP client, the TLS setup and the payload encoding are all exercised and working.
- Most of the remaining work looks like userspace rather than driver, with the possible exception of action-frame pacing.

#### Open questions

- What identity does an iPhone need before it will show the prompt? Everything beneath `/Ask` works and the phone displays nothing, which points at the sender certificate and the Apple ID material rather than the radio.
- Can a Mac be made to publish its `_airdrop._tcp` advert without a BLE wake? Until it does, there is no endpoint to open a socket to.
- In the Mac runs that did have an endpoint, why is the SYN unanswered — is our `awdl_peer_op` entry for that Mac correct? We have only recently learned the entry can be read back, and it has never been checked in a send attempt.
- How does action-frame TX pace against an in-flight transfer? That path has only ever been exercised as a receiver, where our action frames compete with inbound data rather than outbound.

## 3. The regulatory domain is not reapplied to a fresh wiphy

#### What we know

- After a module reload the wiphy comes back in the world domain while the system domain is unchanged:

```
global
country CA: DFS-FCC
	(5730 - 5850 @ 80), (N/A, 36), (N/A), AUTO-BW    ← ch157 allowed

phy#19
country 99: DFS-UNSET
	(5460 - 5860 @ 160), (6, 20), (N/A)              ← the card itself
```

- In the world domain 5 GHz cannot initiate radiation until a beacon has been heard. NetworkManager's autoconnect times out before that happens, so the link needs a manual Wi-Fi cycle after every reload — four times in a single session.
- On 2.4 GHz the world domain still permits active scanning, and the link returns unattended.
- Firmware regulatory has been investigated as a 5 GHz *transmit* gate and retired as a dead end. This entry is about the wiphy's domain and the association it blocks, which is a different thing and is unresolved.

#### Open questions

- Where is the country meant to be reapplied — CLM download on firmware attach, or a `regulatory_hint` after the wiphy is registered?
- Is the world domain transient and merely slower than NetworkManager's patience, or does it persist until something external triggers it?
- Does this explain the 5 GHz receive failure above? Both involve 5 GHz being unusable after a reload, and neither has been ruled out as a symptom of the other.

## 4. `awdl0` loses `IFF_UP` across an `awdl=0/1` cycle

#### What we know

- Each disable drops the netdev and nothing raises it again. Userspace currently re-asserts the link after every toggle.
- The resulting state is convincingly disguised: the firmware transmits normally with action-frame counters climbing, while the interface has no `IFF_UP`, no link-local, and nothing can bind to it. A radio in this state looks alive by every counter and dead by every test.
- Three of the void 5 GHz trials above were this bug, and its symptoms were read as a coexistence failure.
- `awdl0`'s `rx_packets` counts **data** frames only. Action frames — what discovery runs on — never increment it, so "tx 619, rx 0" reads as total deafness while the driver is taking about eight action frames a second from a peer.

#### Open questions

- Should the driver preserve interface state across an `awdl` toggle, or is dropping the netdev the intended contract with userspace expected to re-raise it?
- Is the drop the firmware's doing or the driver's?

## 5. `awdl=0` with a PSF template loaded wedges the firmware

#### What we know

- Setting `awdl=0` while a PSF template is loaded hangs the firmware, so userspace refuses that toggle whenever a template is present.
- The cost of that guard is that a parked data path cannot be recovered without a module reload.
- Clearing the template first is not an escape: a zero-length `awdl_payload` SET is rejected `BCME_BADARG` with the readback unchanged, and any replacement write still reads back 512 bytes, so "a template is loaded" stays true regardless of what is written.
- Once the data path parks with a template loaded, retrying accomplishes nothing — five consecutive attempts produced byte-identical failures. Only a module reload or a reboot clears it.

#### Open questions

- What in the firmware hangs on `awdl=0` with a template present?
- Is there an iovar that resets the AWDL data path without unloading the module? That would remove the last unrecoverable state in the driver.
- Is there any way to make `awdl_payload` report "no template" again short of a reload?

## 6. The instrumentation logs unconditionally

#### What we know

- Patches 0009–0011 are the only oracle for what the firmware is doing.
- The data-path gate counts their per-frame output, so a build without them cannot tell a parked radio from a working one.
- They log on every frame, which is a lot of dmesg for a release build.

#### Open questions

- Is there a form — `dyndbg`, a module parameter, a tracepoint — that keeps the oracle available on demand without the steady-state volume?

## 7. The kernel tag is pinned

#### What we know

- The patches are against `asahi-7.1.13-2`, and the `PKGBUILD` pins it. Newer trees have not been tried.
- The pin is deliberate rather than neglect: an unpinned build turns a rebase conflict into a runtime surprise on hardware whose only network is the thing being patched.

#### Open questions

- What breaks on the next tag? A rebase is only useful alongside a retest.

## 8. Only BCM4387 has been tested

#### What we know

- Developed on BCM4387 (`14e4:4433`) in a MacBook Pro 16-inch, M1 Pro.
- BCM4377, BCM4378 and BCM4388 are plausible — the approach depends on the firmware implementing AWDL, which these parts are believed to do — and entirely untested.
- Reports with the PCI ID and `dmesg` are informative even when they fail; a clean "does not attach" narrows the space as much as a success does.

#### Open questions

- How much of the configuration is BCM4387-specific? The iovar names, the template layout and the channel-sequence encoding were all derived on one part.

## 9. Power cost is unmeasured

#### What we know

- AWDL keeps the radio dwelling on a schedule for the whole of a discoverable window.

#### Open questions

- What does an idle discoverable window cost in battery?
- Does that answer change what a sensible default window length is?

## 10. An active receive window cannot be rescheduled

#### What we know

- **Stay visible for** currently selects the next receive window. Moving it while Omdrop is active does not change the running window.
- Changing the selection while Omdrop is active should reset the running window from that moment. For example, choosing **10 minutes** should leave 10 minutes, not preserve the previous deadline or subtract time already elapsed.
- The root radio helper already supports replacing a live deadline when `start` is called again. The plugin still needs to reschedule its user timer and keep the panel, receiver, watcher, and radio on one deadline.

#### Open questions

- How should the two non-timed endpoints behave mid-window: should **One file, then off** replace the timer with the one-file watcher, and should **Until I turn it off** remove the timer immediately?
- If rescheduling either the user timer or the root radio deadline fails, which existing deadline should remain authoritative and what should the panel report?
