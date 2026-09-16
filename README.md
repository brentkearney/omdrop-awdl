# omdrop-awdl

AWDL support for the Broadcom BCM4387: eleven patches to `brcmfmac`, a DKMS package that builds them, and the root-side helpers that drive the result.

AWDL — Apple Wireless Direct Link — is the link layer AirDrop and AirPlay run over. These patches create an `awdl0` interface from the firmware's own AWDL implementation, rather than reimplementing the protocol in userspace.

This is the driver half of an upcoming "Omdrop" plugin for [Omarchy M](https://github.com/omacom/omarchy-mac), the Omarchy Linux distribution for Macs.

- [Hardware](#hardware)
- [Install](#install)
- [What the package installs](#what-the-package-installs)
- [Configuration](#configuration)
- [DKMS Failsafe](#dkms-failsafe)
- [The patches](#the-patches)
- [Maintenance](#maintenance)
  - [Contributions & WIP](#contributions--wip)
- [Provenance](#provenance)
- [Trademark](#trademark)
- [Licence](#licence)

## Hardware

Developed on **BCM4387** (`14e4:4433`) in a MacBook Pro 16-inch, M1 Pro, running Asahi Linux. Other Apple Broadcom parts are plausible and untested.

This will not work on Intel, MediaTek or Qualcomm Wi-Fi. The approach depends on the firmware already implementing AWDL; the patches configure it rather than providing it. If you want AirDrop on non-Apple hardware, look at [owl](https://github.com/seemoo-lab/owl) and [OpenDrop](https://github.com/seemoo-lab/opendrop), which reimplement AWDL in userspace over monitor mode.

## Install

```bash
git clone https://github.com/brentkearney/omdrop-awdl.git
cd omdrop-awdl
makepkg -si
```

The build is offline and takes seconds: the three kernel directories DKMS compiles are vendored in `kernel/` (1.4 MB, pristine from the pinned Asahi tag — see [kernel/PROVENANCE.md](kernel/PROVENANCE.md)), and the patches are applied to a copy of them at build time.

Not on the AUR: it is an x86_64 index, and no Apple Silicon kernel package lives there. The recipe Omarchy's own package repository builds is generated into [omarchy-pkgs/](omarchy-pkgs/) from the `PKGBUILD` above, so there is one source of truth for how this package is built.

Then reboot, or reload the driver when the link can go down for a minute:

```bash
sudo modprobe -r brcmfmac_wcc brcmfmac brcmutil
sudo modprobe brcmfmac
```

Enable the boot-time interface setup, which creates and configures `awdl0` without enabling AWDL or advertising anything:

```bash
sudo systemctl enable --now awdl0.service
```

Discoverability stays off until something asks for it. The omdrop plugin's panel button is the usual caller; from a terminal:

```bash
pkexec /usr/lib/omdrop/omdrop-discoverable start 600   # visible for ten minutes
pkexec /usr/lib/omdrop/omdrop-discoverable stop
/usr/lib/omdrop/omdrop-discoverable status --json      # no root needed
```

## What the package installs

| Path | What it is |
|---|---|
| `/usr/src/brcmfmac-awdl-<ver>/` | Patched `brcmfmac` sources; DKMS builds them for every kernel |
| `/usr/lib/omdrop/omdrop-discoverable` | Opens and closes a bounded discoverability window: data-path gate, PSF template, announcer, mDNS responder, peer registration |
| `/usr/lib/omdrop/awdl-up` | Creates and configures `awdl0`; run at boot by `awdl0.service` |
| `/usr/lib/omdrop/*.py` | The iovar, frame-building and decoding helpers the two above call |
| `/usr/share/polkit-1/actions/org.omarchy.omdrop.policy` | Action `org.omarchy.omdrop.discover`, bound to `omdrop-discoverable`, so a desktop session can become discoverable without a password |
| `/usr/lib/systemd/system/awdl0.service` | Boot-time `awdl0` setup. Not enabled by the install |
| `/usr/lib/modprobe.d/brcmfmac-awdl.conf` | `debug=0x1000`, which the data-path gate counts `awdl txstatus` lines from |
| `/usr/lib/NetworkManager/conf.d/99-awdl-unmanaged.conf` | Keeps NetworkManager off `awdl0` |
| `/usr/share/doc/brcmfmac-awdl-dkms/` | This README, and an optional `10-wld0.link` the package does not activate |

Boot creates `awdl0` but does **not** enable AWDL. An always-on AWDL makes the radio follow its slot schedule across channels, leaving the infra channel periodically — battery and STA throughput spent continuously for a feature used in bursts — and permanent discoverability is a privacy posture nobody asked for. The cost of the choice is that the first window pays the data-path gate, 15–60 s, instead of the boot paying it.

## Configuration

Nothing is required. Every tunable has a working default, and the helpers read the machine's own MAC, Wi-Fi interface and hostname at runtime rather than carrying a baked-in copy. To override one, drop a one-line file in `/etc/omdrop/`:

| File | Default | Meaning |
|---|---|---|
| `infra-iface` | first Broadcom Wi-Fi interface | The interface `awdl0` is derived from |
| `awdl-host` | `<short hostname>-awdl` | The name advertised over AWDL and answered over mDNS |
| `master-chan` | `6` | Master availability-window channel |
| `peer-chan` | `44` | Peer availability-window channel |
| `chan-shape` | `dense` | Slot density: `sparse`, `dense`, `full`, `dense44`, `mirror` |
| `election-metric` | `100` | The election metric advertised and enforced |
| `rssi-sync-threshold` | firmware default | dBm floor for adopting a peer as root; `-60` keeps the election to the room |

## DKMS Failsafe

DKMS (Dynamic Kernel Module Support) installs the patched module to `updates/dkms/`, which `depmod` prefers over the in-tree driver **without deleting the original**. If a kernel update breaks the out-of-tree build, the stock `brcmfmac` loads and Wi-Fi still works. You lose AWDL, never the network.

## The patches

| | |
|---|---|
| 0001–0005 | Create and manage the `awdl0` interface |
| 0006 | Firmware RAM snapshot vendor op |
| 0007 | Translate AWDL data frames at the `awdl0` boundary |
| 0008 | Tolerate txstatus for a freed flowring (fixes a NULL deref) |
| 0009–0011 | Action-frame instrumentation on the AWDL interface |

**The instrumentation patches matter.** 0009–0011 log and dump AWDL action frames — the PSF and MIF frames discovery actually runs on. Almost nothing about this protocol is documented, and these are how you find out what the firmware is really doing. If you are extending this work, start there.

Patch 0008 is an ordinary kernel bug fix with no AWDL dependency, and stands on its own.

## Maintenance

The patches are against **`asahi-7.1.13-2`** (commit `13aba96f`), matching `linux-asahi 7.1.13.asahi2-1`, and that is the tree vendored in `kernel/`. The pin is deliberate: a newer tree may need them rebased, and building against whatever happens to be current would turn a rebase conflict into a runtime surprise. [kernel/PROVENANCE.md](kernel/PROVENANCE.md) has the re-vendoring sequence.

They touch twelve files, all under `drivers/net/wireless/broadcom/brcm80211/brcmfmac/`. Nothing outside that directory.

The debug build is not a leftover. The data-path gate counts per-frame `awdl txstatus` lines to tell a parked radio from a working one, so a non-debug build removes the only oracle there is.

### Contributions & WIP

Contributions are welcome - feel free to post Issues and PRs. See the [TODO.md](TODO.md) file for a list of the work left to do.

The two largest gaps: receive is stable only with the Wi-Fi link on 2.4 GHz, and there is no send mode yet. Testers on Apple Broadcom parts other than BCM4387 are also useful.

## Provenance

The patches and the helpers were developed with AI assistance.

They were not submitted to Asahi Linux, whose [generative AI policy](https://asahilinux.org/llm-policy/) forbids AI-assisted contributions. That is their call and this repository is not an argument with it — it exists so the work is available to people who want it, under the same licence as the code it derives from.

The kernel patches derive from the Asahi Linux kernel tree and are licensed **GPL-2.0-only**, as the kernel is; authorship and `Signed-off-by` lines are preserved in each patch. The userspace helpers are original work under the same licence. They were written from published field descriptions — Broadcom's `wlioctl.h`, the Wireshark AWDL dissector, SEEMOO's papers — and from this project's own measurements, not from [owl](https://github.com/seemoo-lab/owl)'s GPLv3 source.

## Trademark

AirDrop is a trademark of Apple Inc. Omdrop is an independent project and is not affiliated with or endorsed by Apple.

## Licence

GPL-2.0-only. See [LICENSE](LICENSE).
