# omdrop-awdl

AWDL support for the Broadcom BCM4387, as eleven patches to `brcmfmac` and a DKMS package that builds them.

AWDL — Apple Wireless Direct Link — is the link layer AirDrop and AirPlay run over. These patches create an `awdl0` interface from the firmware's own AWDL implementation, rather than reimplementing the protocol in userspace.

This is the driver half of an upcoming "Omdrop" plugin for [Omarchy M](https://github.com/omacom/omarchy-mac), the Omarchy Linux distribution for Macs.

## Hardware

Verified on **BCM4387** (`14e4:4433`) in a MacBook Pro 16-inch, M1 Pro, running Asahi Linux. Other Apple Broadcom parts are plausible and untested.

This will not work on Intel, MediaTek or Qualcomm Wi-Fi. The approach depends on the firmware already implementing AWDL; the patches configure it rather than providing it. If you want AirDrop on non-Apple hardware, look at [owl](https://github.com/seemoo-lab/owl) and [OpenDrop](https://github.com/seemoo-lab/opendrop), which reimplement AWDL in userspace over monitor mode.

## Install

```bash
git clone https://github.com/brentkearney/omdrop-awdl.git
cd omdrop-awdl
makepkg -si
```

`makepkg` clones the Asahi kernel at the pinned tag, so the first build downloads a full kernel tree and takes a while.

Then reboot, or reload the driver when the link can go down for a minute:

```bash
sudo modprobe -r brcmfmac_wcc brcmfmac brcmutil && sudo modprobe brcmfmac
```

## How it fails

Deliberately, toward a working machine. DKMS installs the patched module to `updates/dkms/`, which `depmod` prefers over the in-tree driver **without deleting it**. If a kernel update breaks the out-of-tree build, the stock `brcmfmac` loads and Wi-Fi still works. You lose AWDL, never the network.

That is the reason this is a DKMS package rather than a hand-built module: a kernel update that silently removes your Wi-Fi is not a thing to leave lying around for someone to debug.

## The patches

| | |
|---|---|
| 0001–0005 | Create and manage the `awdl0` interface the way Apple's driver does |
| 0006 | Firmware RAM snapshot vendor op |
| 0007 | Translate AWDL data frames at the `awdl0` boundary |
| 0008 | Tolerate txstatus for a freed flowring (fixes a NULL deref) |
| 0009–0011 | Action-frame instrumentation on the AWDL interface |

**The instrumentation patches matter.** 0009–0011 log and dump AWDL action
frames — the PSF and MIF frames discovery actually runs on. Almost nothing about this protocol is documented, and these are how you find out what the firmware is really doing. If you are extending this work, start there.

Patch 0008 is an ordinary kernel bug fix with no AWDL dependency, and stands on its own.

## Maintenance

The patches are against **`asahi-7.1.13-2`**, matching `linux-asahi
7.1.13.asahi2-1`, and the `PKGBUILD` pins that tag. The pin is deliberate: a
newer tree may need them rebased, and building against whatever happens to be current would turn a rebase conflict into a runtime surprise.

They touch twelve files, all under `drivers/net/wireless/broadcom/brcm80211/brcmfmac/`. Nothing outside that directory.

The debug build is not a leftover. The data-path gate counts per-frame `awdl txstatus` lines to tell a parked radio from a working one, so a non-debug build removes the only oracle there is.

## Provenance

These patches were developed with AI assistance.

They were not submitted to Asahi Linux, whose [generative AI policy](https://asahilinux.org/llm-policy/) forbids AI-assisted contributions. That is their call and this repository is not an argument with it — it exists so the work is available to people who want it, under the same licence as the code it derives from.

Derived from the Asahi Linux kernel tree and licensed **GPL-2.0-only**, as the kernel is. Authorship and `Signed-off-by` lines are preserved in each patch.

## Licence

GPL-2.0-only. See [LICENSE](LICENSE).
