# TODO

Known gaps in `omdrop-awdl`, with what has already been tested, with supporting information.

The instrumentation patches (0009–0011) are how most of this was found, and their output is the evidence behind nearly everything below.

Each section separates what was observed from what is still unknown. Where a finding rests on a single trial, it says so.

- [Receive with the Wi-Fi link on 5 GHz](#receive-with-the-wi-fi-link-on-5-ghz)
- [Send mode does not exist](#send-mode-does-not-exist)
- [The regulatory domain is not reapplied to a fresh wiphy](#the-regulatory-domain-is-not-reapplied-to-a-fresh-wiphy)
- [`awdl0` loses `IFF_UP` across an `awdl=0/1` cycle](#awdl0-loses-iff_up-across-an-awdl01-cycle)
- [`awdl=0` with a PSF template loaded wedges the firmware](#awdl0-with-a-psf-template-loaded-wedges-the-firmware)
- [The instrumentation logs unconditionally](#the-instrumentation-logs-unconditionally)
- [The kernel tag is pinned](#the-kernel-tag-is-pinned)
- [Only BCM4387 has been tested](#only-bcm4387-has-been-tested)
- [Patch 0008 is an independent kernel fix](#patch-0008-is-an-independent-kernel-fix)
- [Power cost is unmeasured](#power-cost-is-unmeasured)

## Receive with the Wi-Fi link on 5 GHz

#### What we know

- Receive is proven with the infrastructure link on 2.4 GHz: files arrive byte-exact from both a Mac and an iPhone.
- That is narrower than "AWDL works on 2.4 GHz only". In the working configuration the AWDL dwell is already mostly 5 GHz — 12 of 16 slots on channel 157, two on 149, one on the channel 6 master slot — while only the *infrastructure* link is on 2.4 GHz. The transfers were carried on 5 GHz AWDL.
- With `wld0` associated on 5 GHz ch157 and every other condition individually verified — `awdl0` up with a link-local, receiver listening, action frames moving both ways, the peer at −41 dBm with `dist=0`, and our advertised channel sequence read back from the firmware — a sender never discovered us.
- That failure rests on **one** valid trial. The configuration was attempted four times, but three attempts ran while `awdl0` had been left administratively down by an `awdl=0/1` cycle (below), so they measured a dead netdev rather than the band. One of those three reported the firmware as parked while it was transmitting normally.
- A firmer result, from a deliberate A/B: moving the dwell to 2.4-dominant — thirteen of sixteen slots on channel 6 — broke discovery outright, even though that sequence still overlapped the peer on both 149 slots and on the shared channel 6 master slot. Overlap on secondary slots plus the master availability window was not sufficient; the peer's primary social channel had to be in our dwell.
- Apple keeps the master availability window on channel 6 even with a 5 GHz infrastructure link, and so do we.

#### Open questions

- Does the single 5 GHz failure reproduce? Three void attempts corroborate nothing.
- Does the firmware actually dwell on the sequence we advertise while the STA holds a 5 GHz channel? If the STA pins the radio, the advertised slots are a fiction and a peer scheduling into them finds nobody.
- Is the collision what matters? Every 5 GHz trial used an infrastructure channel identical to the AWDL peer channel, because the only AP available offered ch157. "5 GHz infra" and "5 GHz infra on the same channel" have never been separated.
- Is this the same fault as the regulatory-domain bug below, or a second one?

## Send mode does not exist

#### What we know

- Receive only. Nothing here sends.
- Most of what is missing is userspace, not driver.

#### Open questions

- How does action-frame TX pace against an in-flight transfer? That path has only been exercised as a receiver, where our action frames compete with inbound data rather than outbound.

## The regulatory domain is not reapplied to a fresh wiphy

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

#### Open questions

- Where is the country meant to be reapplied — CLM download on firmware attach, or a `regulatory_hint` after the wiphy is registered?
- Is the world domain transient and merely slower than NetworkManager's patience, or does it persist until something external triggers it?
- Does this explain the 5 GHz receive failure above? Both involve 5 GHz being unusable after a reload, and neither has been ruled out as a symptom of the other.

## `awdl0` loses `IFF_UP` across an `awdl=0/1` cycle

#### What we know

- Each disable drops the netdev and nothing raises it again. Userspace currently re-asserts the link after every toggle.
- The resulting state is convincingly disguised: the firmware transmits normally with action-frame counters climbing, while the interface has no `IFF_UP`, no link-local, and nothing can bind to it. A radio in this state looks alive by every counter and dead by every test.
- Three of the void 5 GHz trials above were this bug, and its symptoms were read as a coexistence failure.
- `awdl0`'s `rx_packets` counts **data** frames only. Action frames — what discovery runs on — never increment it, so "tx 619, rx 0" reads as total deafness while the driver is taking about eight action frames a second from a peer.

#### Open questions

- Should the driver preserve interface state across an `awdl` toggle, or is dropping the netdev the intended contract with userspace expected to re-raise it?
- Is the drop the firmware's doing or the driver's?

## `awdl=0` with a PSF template loaded wedges the firmware

#### What we know

- Setting `awdl=0` while a PSF template is loaded hangs the firmware, so userspace refuses that toggle whenever a template is present.
- The cost of that guard is that a parked data path cannot be recovered without a module reload.
- Clearing the template first is not an escape: a zero-length `awdl_payload` SET is rejected `BCME_BADARG` with the readback unchanged, and any replacement write still reads back 512 bytes, so "a template is loaded" stays true regardless of what is written.
- Once the data path parks with a template loaded, retrying accomplishes nothing — five consecutive attempts produced byte-identical failures. Only a module reload or a reboot clears it.

#### Open questions

- What in the firmware hangs on `awdl=0` with a template present?
- Is there an iovar that resets the AWDL data path without unloading the module? That would remove the last unrecoverable state in the driver.
- Is there any way to make `awdl_payload` report "no template" again short of a reload?

## The instrumentation logs unconditionally

#### What we know

- Patches 0009–0011 are the only oracle for what the firmware is doing.
- The data-path gate counts their per-frame output, so a build without them cannot tell a parked radio from a working one.
- They log on every frame, which is a lot of dmesg for a release build.

#### Open questions

- Is there a form — `dyndbg`, a module parameter, a tracepoint — that keeps the oracle available on demand without the steady-state volume?

## The kernel tag is pinned

#### What we know

- The patches are against `asahi-7.1.13-2`, and the `PKGBUILD` pins it. Newer trees have not been tried.
- The pin is deliberate rather than neglect: an unpinned build turns a rebase conflict into a runtime surprise on hardware whose only network is the thing being patched.

#### Open questions

- What breaks on the next tag? A rebase is only useful alongside a retest.

## Only BCM4387 has been tested

#### What we know

- Developed on BCM4387 (`14e4:4433`) in a MacBook Pro 16-inch, M1 Pro.
- BCM4377, BCM4378 and BCM4388 are plausible — the approach depends on the firmware implementing AWDL, which these parts are believed to do — and entirely untested.
- Reports with the PCI ID and `dmesg` are informative even when they fail; a clean "does not attach" narrows the space as much as a success does.

#### Open questions

- How much of the configuration is BCM4387-specific? The iovar names, the template layout and the channel-sequence encoding were all derived on one part.

## Patch 0008 is an independent kernel fix

#### What we know

- The freed-flowring txstatus fix is an ordinary NULL-deref bug fix with no AWDL dependency.
- It stands on its own and benefits anyone using this driver, not only this project.
- It has not been submitted upstream and cannot be from here: see Provenance in the README.

#### Open questions

- Is anyone willing to review it and carry it upstream under their own authorship?

## Power cost is unmeasured

#### What we know

- AWDL keeps the radio dwelling on a schedule for the whole of a discoverable window.

#### Open questions

- What does an idle discoverable window cost in battery?
- Does that answer change what a sensible default window length is?
