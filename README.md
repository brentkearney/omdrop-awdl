# omdrop-awdl

AWDL protocol activation for Apple Silicon Macs on Linux: twelve patches to `brcmfmac`, a DKMS package that builds them, and helpers.

AWDL (Apple Wireless Direct Link) is the link layer AirDrop and AirPlay run over. These patches expose the Wi-Fi firmware's own AWDL implementation as an `awdl0` interface, instead of reimplementing the protocol in userspace.

This is the driver half of [Omdrop](https://github.com/brentkearney/omdrop-plugin), AirDrop for [Omarchy M](https://github.com/omacom/omarchy-mac). The functional patches are also proposed for Omarchy's `linux-aurora` kernel in [aurora-silicon/linux#24](https://github.com/aurora-silicon/linux/pull/24).

- [Install](#install)
- [What the package installs](#what-the-package-installs)
- [Sending and receiving](#sending-and-receiving)
- [Configuration](#configuration)
- [Power](#power)
- [The patches](#the-patches)
- [Hardware compatibility](#hardware-compatibility)
- [Maintenance](#maintenance)
- [Provenance](#provenance)


## Install

```bash
git clone https://github.com/brentkearney/omdrop-awdl.git
cd omdrop-awdl
makepkg -si
```

The build is offline and takes seconds. The kernel sources DKMS compiles are vendored in `kernel/`, taken unmodified from the pinned Asahi tag (see [kernel/PROVENANCE.md](kernel/PROVENANCE.md)), and the patches are applied to a copy at build time. The package isn't on the AUR, which only indexes x86_64. The recipe proposed for Omarchy's package repository is generated into [omarchy-pkgs/](omarchy-pkgs/) from the same `PKGBUILD`.

Reboot, or reload the driver if the link can drop for a minute:

```bash
sudo modprobe -r brcmfmac_wcc brcmfmac brcmutil
sudo modprobe brcmfmac
```

Then enable boot-time setup, which creates `awdl0` without enabling AWDL or advertising anything:

```bash
sudo systemctl enable --now awdl0.service
```

If a kernel update breaks the DKMS build, the stock `brcmfmac` loads instead: DKMS installs to `updates/dkms/` without removing the in-tree driver. You lose AWDL, not the network.

## What the package installs

| Path | Purpose |
|---|---|
| `/usr/src/brcmfmac-awdl-<ver>/` | Patched `brcmfmac` sources, built by DKMS for every kernel |
| `/usr/lib/omdrop/omdrop-discoverable` | Opens and closes a bounded discoverability window |
| `/usr/lib/omdrop/awdl-up` | Creates and configures `awdl0`; run at boot by `awdl0.service` |
| `/usr/lib/omdrop/send-to-peer` | Lists peers and sends files to one |
| `/usr/lib/omdrop/airdrop-send.py` | The sender: `/Discover`, `/Ask`, and `/Upload` over TLS on `awdl0` |
| `/usr/lib/omdrop/awdl-resolve` | Reads a peer's advertised AirDrop service from its action frames |
| `/usr/lib/omdrop/*.py` | Helpers for the tools above |
| `/usr/share/polkit-1/actions/org.omarchy.omdrop.policy` | Lets a local desktop session run `omdrop-discoverable` without a password |
| `/usr/lib/systemd/system/awdl0.service` | Boot-time `awdl0` setup; not enabled by the install |
| `/usr/lib/modprobe.d/brcmfmac-awdl.conf` | `debug=0x1000`, the log lines the data-path check counts |
| `/usr/lib/NetworkManager/conf.d/99-awdl-unmanaged.conf` | Keeps NetworkManager off `awdl0` |
| `/usr/share/doc/brcmfmac-awdl-dkms/` | This README, and an optional `10-wld0.link` the package doesn't activate |

Boot creates `awdl0` but doesn't enable AWDL. AWDL that is always on makes the radio hop channels on its slot schedule, which costs battery and Wi-Fi throughput, and leaves the machine permanently discoverable. The trade-off is that the first window takes about ten seconds to come up.

## Sending and receiving

Both directions work with Macs and iPhones, in Everyone and Contacts Only mode. Only the radio window needs root, which polkit grants without a password.

A window turns discoverability on for a set time:

```bash
pkexec /usr/lib/omdrop/omdrop-discoverable start 600   # ten minutes
pkexec /usr/lib/omdrop/omdrop-discoverable stop
/usr/lib/omdrop/omdrop-discoverable status --json      # no root needed
```

### Receiving

This package is the radio side only. The receiving HTTPS service is in the [Omdrop plugin](https://github.com/brentkearney/omdrop-plugin), where `omdrop on 10m` opens a window and starts the receiver together. Any AirDrop receiver listening on `[<awdl0 link-local>]:8771` works.

To send to this machine from a Contacts Only device, this machine needs an Apple-issued identity, which the sender checks it against. Without one, set the sender to **Everyone**; a self-signed certificate is rejected at TLS.

### Sending

With a window open:

```bash
/usr/lib/omdrop/send-to-peer --list               # peers in range
/usr/lib/omdrop/send-to-peer FILE                 # the only peer heard
/usr/lib/omdrop/send-to-peer --mac e2:9d:.. FILE  # choose a peer
/usr/lib/omdrop/send-to-peer --wait 120 FILE      # keep trying while an iPhone sleeps
/usr/lib/omdrop/send-to-peer FILE1 FILE2 ...      # several files, as one transfer
```

The recipient's prompt shows the `--name` value, `Omarchy` by default. Several files go as one transfer, so the recipient accepts them once. Files must have different names, because the recipient stores each under its name alone. `--list` leaves out this machine's own AWDL address, which the firmware's tables can include.

The sender doesn't wait for mDNS. A peer's address is the EUI-64 link-local of its AWDL MAC, and its port comes from the service it advertises. A Mac with no Finder AirDrop window open publishes nothing over mDNS but still listens, so waiting for mDNS would miss it. An iPhone opens its listener only for a few seconds around other AirDrop activity, so `--wait` polls and sends the moment it opens. Opening a share sheet on the phone brings its listener up.

### Naming a peer

`send-to-peer --list --names` (or `omdrop peers -n`) asks each peer for its name with a `/Discover` request, because AirDrop advertises no names. A Contacts Only device keeps its AirDrop service shut until it recognizes a nearby sender. So during the lookup, the sender broadcasts a Bluetooth advert carrying this machine's contact hashes, as an Apple device does when its share sheet opens, and stops it when the lookup ends. `--no-wake` skips the advert, and then only devices already listening answer.

The advert needs an Apple-issued identity, BlueZ, and `python-dbus` and `python-gobject`. Without them, the lookup says so on stderr and names only devices already listening.

In the output, `(no response)` means nothing answered on the AirDrop port. `(anonymous)` means the peer answered but withheld its name, as it does when it doesn't recognize the sender. A lookup can miss a device that is awake, so if a device you expect is unnamed, run it again.

### The firmware peer table holds eight entries

The firmware tracks at most eight peers, and a peer without an entry silently loses every frame sent to it. Apple devices change their AWDL MAC every few minutes, and each change takes a new entry. The peer watcher frees slots when a window starts, on `SIGTERM`, and at capacity, where it evicts the peer heard least recently. The device you're talking to stays registered, but in a busy room a quiet device can drop out and reappear. `omdrop-discoverable status` reports `peer_op=degraded` when the table is full of peers that are all audible.

### Diagnosing a transfer

```bash
pkexec /usr/lib/omdrop/omdrop-discoverable peers      # firmware peer table, with RSSI
pkexec /usr/lib/omdrop/omdrop-discoverable diag 10    # counters over ten seconds
pkexec /usr/lib/omdrop/omdrop-discoverable trace on   # per-frame tx completions in dmesg
pkexec /usr/lib/omdrop/omdrop-discoverable trace off
```

With tracing on, `tx_status=0x0000` means the peer acknowledged a frame, and `0x0003` means the firmware dropped it before transmission, usually because the peer isn't registered.

## Configuration

Nothing is required. The helpers read the MAC, Wi-Fi interface, and hostname at runtime. To override a default, put a one-line file in `/etc/omdrop/`:

| File | Default | Meaning |
|---|---|---|
| `infra-iface` | first Broadcom Wi-Fi interface | The interface `awdl0` is derived from |
| `awdl-host` | `<short hostname>-awdl` | Name advertised over AWDL and answered over mDNS |
| `master-chan` | `6` | Master availability-window channel |
| `peer-chan` | `44` | Peer availability-window channel |
| `chan-shape` | `dense` | Slot density: `sparse`, `dense`, `full`, `dense44`, or `mirror` |
| `election-metric` | `100` | Election metric advertised and enforced |
| `rssi-sync-threshold` | firmware default | dBm floor for adopting a peer as root; `-60` keeps the election to the room |

## Power

An open window costs 71 mW (95% CI 59.8–82.5), measured on an M1 Pro over 5.6 hours of alternating six-minute blocks. That's 1.5% of a 4.71 W idle machine: a ten-minute window costs about 19 seconds of battery, and staying discoverable all day costs 2% of a charge. How long to stay visible is a privacy question, not a battery one. The method and data are in [issue #8](https://github.com/brentkearney/omdrop-awdl/issues/8#issuecomment-5731084807).

## The patches

| Patches | Purpose |
|---|---|
| 0001–0005 | Create and manage the `awdl0` interface |
| 0006 | Firmware RAM snapshot vendor op |
| 0007 | Translate AWDL data frames at the `awdl0` boundary |
| 0008 | Tolerate txstatus for a freed flowring (fixes a NULL dereference) |
| 0009–0011 | Log and dump AWDL action frames |
| 0012 | Gate that instrumentation behind the `awdl_trace` module parameter |

The kernel pull request carries the functional path: 0001–0005, 0007, and 0008. Patch 0008 is an ordinary bug fix that stands on its own. The instrumentation patches, 0009–0011, show the PSF and MIF frames discovery runs on. Little about this protocol is documented, so start there if you're extending this work.

## Hardware compatibility

List your Wi-Fi hardware by running `lspci -nn | grep -i network`. BCM4378 is `14e4:4425`, BCM4387 is `14e4:4433`, and BCM4388 is `14e4:4434`.

The patches configure AWDL the firmware already implements, so they only work on Apple's Broadcom Wi-Fi, not on Intel, MediaTek, or Qualcomm cards. For AirDrop on other hardware, see [OpenDrop](https://github.com/seemoo-lab/opendrop) and [owl](https://github.com/seemoo-lab/owl).

### Known to work

| Mac | Wi-Fi chip | WLAN PCI ID | Verified | Report |
| --- | --- | --- | --- | --- |
| MacBook Pro 16-inch (2021), M1 Pro | BCM4387 | `14e4:4433` | Sending and receiving, iPhone and Mac, Everyone and Contacts Only | |
| MacBook Air (2020), M1 | BCM4378 | `14e4:4425` | Receiving from an iPhone, Everyone | [omdrop-plugin#12](https://github.com/brentkearney/omdrop-plugin/issues/12) |
| MacBook Pro 16-inch (2023), M2 Pro | BCM4388 | `14e4:4434` | Receiving from an iPhone, Everyone | [#16](https://github.com/brentkearney/omdrop-awdl/issues/16) |

### Probably works

Same Wi-Fi chip as a Mac above, not yet tested:

| Mac | Apple chip | Wi-Fi chip |
| --- | --- | --- |
| MacBook Pro 14-inch (2021) | M1 Pro, M1 Max | BCM4387 |
| MacBook Pro 16-inch (2021) | M1 Max | BCM4387 |
| Mac Studio (2022) | M1 Max, M1 Ultra | BCM4387 |
| MacBook Air 13-inch (2022) | M2 | BCM4387 |
| MacBook Air 15-inch (2023) | M2 | BCM4387 |
| MacBook Pro 13-inch (2020) | M1 | BCM4378 |
| Mac mini (2020) | M1 | BCM4378 |
| iMac 24-inch (2021) | M1 | BCM4378 |
| MacBook Pro 13-inch (2022) | M2 | BCM4378 |
| MacBook Pro 14-inch (2023) | M2 Pro, M2 Max | BCM4388 |
| MacBook Pro 16-inch (2023) | M2 Max | BCM4388 |
| Mac mini (2023) | M2, M2 Pro | BCM4388 |
| Mac Studio (2023) | M2 Max, M2 Ultra | BCM4388 |
| Mac Pro (2023) | M2 Ultra | BCM4388, reported; Asahi's device tree is inconsistent for this model |

Sources: the [Asahi device list](https://asahilinux.org/docs/hw/devices/device-list/), [Asahi's WLAN PCI IDs](https://github.com/AsahiLinux/linux/blob/asahi/drivers/net/wireless/broadcom/brcm80211/include/brcm_hw_ids.h), and an [Apple device-tree radio inventory](https://gist.github.com/JJTech0130/bf7dbc5b4ea1442a07bbd58bb1ae89c4).

Tried it on another Mac? [Send a hardware report](https://github.com/brentkearney/omdrop-awdl/issues/new?template=hardware-report.yml), working or not.

## Maintenance

The patches apply to `asahi-7.1.13-3` (commit `94fb2334`), the tree vendored in `kernel/`, and touch fourteen files, all in `drivers/net/wireless/broadcom/brcm80211/brcmfmac/`. The pin is deliberate, so a rebase conflict shows up at build time rather than at runtime. [kernel/PROVENANCE.md](kernel/PROVENANCE.md) has the re-vendoring steps, and `tests/check-patch-drift` checks whether the patches apply to a newer tag.

The module is built with debug logging because the data-path check counts per-frame `awdl txstatus` lines to tell a stalled radio from a working one.

Issues and pull requests are welcome. Before changing a kernel patch, read [CONTRIBUTING.md](CONTRIBUTING.md): the patches are stored as `git format-patch` files, so sending commits, not edited patch files, keeps your authorship on the commit that goes upstream.

## Provenance

The patches and helpers were developed with AI assistance. They weren't submitted to Asahi Linux, whose [generative AI policy](https://asahilinux.org/llm-policy/) forbids AI-assisted contributions; this repository makes the work available to people who want it.

The kernel patches derive from the Asahi Linux kernel and are licensed GPL-2.0-only, with authorship and `Signed-off-by` lines preserved. The userspace helpers are original work under the same licence, written from published field descriptions (Broadcom's `wlioctl.h`, the Wireshark AWDL dissector, and SEEMOO's papers) and this project's own measurements, not from [owl](https://github.com/seemoo-lab/owl)'s GPLv3 source. The sender, `airdrop-send.py`, used to import [OpenDrop](https://github.com/seemoo-lab/opendrop) (GPLv3); it now makes the same requests with its own code.

## Trademark

AirDrop is a trademark of Apple Inc. Omdrop is an independent project, not affiliated with or endorsed by Apple.

## Licence

GPL-2.0-only. See [LICENSE](LICENSE).
