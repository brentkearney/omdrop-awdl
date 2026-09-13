# TODO

Known gaps in `omdrop-awdl`, with what has already been tested, with supporting information.

The instrumentation patches (0009–0011) are how most of this was found. If you are picking something up, start by reading their output.

- [Stable receive with the Wi-Fi link on 5 GHz](#stable-receive-with-the-wi-fi-link-on-5-ghz)
- [Send mode](#send-mode)
- [Regulatory domain is not reapplied to a fresh wiphy](#regulatory-domain-is-not-reapplied-to-a-fresh-wiphy)
- [`awdl0` loses `IFF_UP` across an `awdl=0/1` cycle](#awdl0-loses-iff_up-across-an-awdl01-cycle)
- [`awdl=0` with a PSF template loaded wedges the firmware](#awdl0-with-a-psf-template-loaded-wedges-the-firmware)
- [Put the instrumentation behind a module parameter](#put-the-instrumentation-behind-a-module-parameter)
- [Rebase onto newer Asahi tags](#rebase-onto-newer-asahi-tags)
- [Testers on other Apple Broadcom parts](#testers-on-other-apple-broadcom-parts)
- [Patch 0008 deserves upstreaming by someone else](#patch-0008-deserves-upstreaming-by-someone-else)
- [Power cost is unmeasured](#power-cost-is-unmeasured)

## Stable receive with the Wi-Fi link on 5 GHz

Receive is proven with the infrastructure link on 2.4 GHz.

Note what that does *not* mean: the AWDL dwell is already mostly 5 GHz in the working configuration — 12 of 16 slots on channel 157. What is unsolved is running the STA on 5 GHz **at the same time**, not 5 GHz AWDL.

With `wld0` associated on 5 GHz ch157 and every other condition individually verified — `awdl0` up with a link-local, receiver listening, action frames flowing both ways, the peer at −41 dBm with `dist=0`, our advertised channel sequence read back from the firmware — a sender still never discovers us. Unexplained.

Be careful how much weight you put on that. The configuration was attempted four times, but three of those ran while `awdl0` had been left administratively down by an `awdl=0/1` cycle (see below), so they measured a dead netdev rather than the band — one of them even reported the firmware as parked, which it was not. Exactly **one** trial is valid: the one taken after that bug was fixed. The failure is real and carefully observed, but it is a single clean observation, not an established pattern.

Cheapest next steps, in order:

1. Repeat the valid trial. One clean negative is thin evidence for something this structural, and the three void attempts do not corroborate it.
2. Compare the advertised channel sequence against what the firmware actually dwells on while the STA holds a 5 GHz channel. The STA may be pinning the radio and starving the AWDL slots we advertise.
3. Try 5 GHz infrastructure on a channel that does **not** collide with the AWDL peer channel. This needs an AP that offers one.

Do not start by changing the master channel to 5 GHz. Making the sequence 2.4-dominant was tested and *broke* discovery outright, even though it still overlapped the peer on two secondary slots and the shared master slot — a peer's primary social channel has to be in our dwell. Apple keeps the master availability window on channel 6 with a 5 GHz infrastructure link, and so do we.

## Send mode

Receive only today. Most of the remaining work is userspace, but the driver side wants review of action-frame TX pacing while a transfer is running.

## Regulatory domain is not reapplied to a fresh wiphy

After a module reload the wiphy comes back as `country 99: DFS-UNSET` — the world domain, 20 dBm cap — while the system domain is, for example, `CA`:

```
global
country CA: DFS-FCC
	(5730 - 5850 @ 80), (N/A, 36), (N/A), AUTO-BW    ← ch157 allowed

phy#19
country 99: DFS-UNSET
	(5460 - 5860 @ 160), (6, 20), (N/A)              ← the card itself
```

In the world domain 5 GHz cannot initiate radiation until a beacon is heard, so NetworkManager's autoconnect times out and the link needs a **manual Wi-Fi cycle after every reload** — four times in one session. On 2.4 GHz the world domain still permits active scanning and it reconnects unattended.

This is a plain bug, it is plausibly entangled with the 5 GHz item above, and it is worth fixing on its own: a driver that cannot survive its own reload unattended is hard to ship.

## `awdl0` loses `IFF_UP` across an `awdl=0/1` cycle

Each disable drops the netdev and nothing raises it again, so the firmware transmits happily — action-frame counters climbing — while the interface has no `IFF_UP` and no link-local and nothing can bind to it.

Userspace currently re-asserts the link. The driver arguably should preserve it, or at least document that it will not.

Worth knowing while debugging: `awdl0`'s `rx_packets` counts **data** frames only. Action frames, which discovery actually runs on, never increment it. A reading of "tx 619, rx 0" looks like total deafness and is not.

## `awdl=0` with a PSF template loaded wedges the firmware

Because of that hang, userspace refuses the toggle whenever a template is loaded — which leaves a parked data path unrecoverable without a module reload.

The template cannot be cleared as an escape either: a zero-length `awdl_payload` SET is rejected `BCME_BADARG` with the readback unchanged, and any replacement write still reads back 512 bytes, so "is a template loaded" stays true.

A driver-side reset of the AWDL data path that does not require unloading the module would remove the last unrecoverable state.

## Put the instrumentation behind a module parameter

Patches 0009–0011 are the only oracle for what the firmware is doing and should not be dropped. They do log unconditionally. A `dyndbg`-friendly or parameter-gated form would let a release build keep the oracle without the dmesg volume.

## Rebase onto newer Asahi tags

Pinned to `asahi-7.1.13-2` deliberately. Each new tag needs a rebase and a retest, not a blind bump.

## Testers on other Apple Broadcom parts

Developed only on BCM4387 (`14e4:4433`), in a MacBook Pro 16-inch M1 Pro. BCM4377/4378/4388 are plausible and completely untested. Reports with the PCI ID and `dmesg` are useful **even when they fail** — a clean "does not attach" is information.

## Patch 0008 deserves upstreaming by someone else

The freed-flowring txstatus fix is an ordinary kernel bug fix with no AWDL dependency. This project cannot submit it to Asahi Linux (see Provenance in the README). A contributor who reviews it and carries it upstream under their own authorship would do the wider ecosystem a favour.

## Power cost is unmeasured

AWDL keeps the radio dwelling on a schedule. Nobody has measured what an idle discoverable window costs in battery.
