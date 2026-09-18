# Maintainer: Brent Kearney <1550934+brentkearney@users.noreply.github.com>
#
# AWDL support for the BCM4387: a DKMS module, plus the root-side userspace the
# omdrop plugin drives it through.
#
# The patched brcmfmac installs to updates/dkms/, which depmod prefers over the
# in-tree module WITHOUT replacing it. That is the whole safety story: if this
# ever fails to build, the stock driver loads and Wi-Fi keeps working. You lose
# AWDL, never the network.

pkgname=brcmfmac-awdl-dkms
pkgver=0.3.0
pkgrel=1
_asahitag=asahi-7.1.13-2
pkgdesc="AWDL (AirDrop link layer) support for the BCM4387: DKMS module and root helpers"
arch=('aarch64')
url="https://github.com/brentkearney/omdrop-awdl"
license=('GPL-2.0-only')
# The helpers are bash and python3 and drive the radio through ip(8), ping(8),
# flock(1), setsid(1) and pgrep(1). polkit is what lets an unprivileged desktop
# session invoke the one helper that needs root.
depends=('dkms' 'bash' 'python' 'iproute2' 'iputils' 'kmod' 'polkit'
         'procps-ng' 'util-linux' 'systemd')
makedepends=()        # prepare() patches with patch(1), which base-devel has
optdepends=('linux-asahi-headers: build against the Asahi kernel'
            'networkmanager: keeps awdl0 unmanaged and settles the Wi-Fi MAC before awdl0 is derived from it'
            'opendrop: the AirDrop receiver the omdrop plugin runs on top of this (AUR; there is no python-opendrop)'
            'bluez: the opt-in BLE Continuity advert'
            'python-dbus: the opt-in BLE Continuity advert'
            'python-gobject: the opt-in BLE Continuity advert')
install="${pkgname}.install"

# The three directories DKMS actually compiles, vendored pristine from Asahi's
# tree at ${_asahitag} (commit ${_asahicommit}) under kernel/. 1.4 MB.
#
# This used to be `git+https://github.com/AsahiLinux/linux.git#tag=...`, which
# makepkg fetches as a full mirror clone -- the entire kernel history, several
# GB over the network -- to read 1.4 MB of it and throw the rest away. The
# module is compiled later by DKMS against the installed kernel headers, never
# against that tree, so nothing outside these directories was ever used.
#
# Re-vendor with:
#   git archive <tag> drivers/net/wireless/broadcom/brcm80211/{brcmfmac,brcmutil,include} | tar -x -C kernel
# and re-check the patches. Keeping the kernel path prefix is what lets them
# apply with -p1 exactly as they would upstream.
_asahicommit=13aba96fb344feb5708d998c54331a719431a3db
# makepkg's source array takes files, not directories, so the vendored tree and
# the rest of the checkout are read from ${startdir} the way they always were.
# prepare() copies the kernel sources into ${srcdir} first: patching in place
# would leave a modified tree in the checkout and make a second build fail.
source=('dkms.conf.in')
sha256sums=('SKIP')

prepare() {
  rm -rf "${srcdir}/kernel"
  cp -a "${startdir}/kernel" "${srcdir}/kernel"
  cd "${srcdir}/kernel"
  # patch(1), NOT `git apply`: this builds inside a git clone (the plugin's
  # installer clones this repo and runs makepkg in it), and `git apply`
  # resolves paths against the enclosing repository's root rather than the
  # working directory. It then reports success having changed nothing, and the
  # package ships pristine sources -- a driver with no AWDL in it.
  for p in "${startdir}"/patches/*.patch; do
    echo "applying ${p##*/}"
    patch -p1 --forward --silent -i "$p"
  done
}

package() {
  local src="${srcdir}/kernel/drivers/net/wireless/broadcom/brcm80211"
  local dest="${pkgdir}/usr/src/brcmfmac-awdl-${pkgver}"

  # Sources go at the ROOT of /usr/src/<pkg>-<ver>/, beside dkms.conf: DKMS
  # copies that directory verbatim into its own build/ and runs make with
  # M=<that dir>, so the kernel Makefile must find Makefile, brcmfmac/ and
  # brcmutil/ directly there.
  install -d "$dest"
  cp -r "$src/brcmfmac" "$src/brcmutil" "$src/include" "$dest/"
  printf 'obj-m += brcmfmac/\nobj-m += brcmutil/\n' > "$dest/Makefile"
  sed "s/@VERSION@/${pkgver}/" "${srcdir}/dkms.conf.in" > "$dest/dkms.conf"

  # The root-side userspace. It lives under /usr/lib because the polkit action
  # below binds the discoverability grant to one exact path: a helper anywhere
  # the invoking user can write to would hand that user root.
  local lib="${pkgdir}/usr/lib/omdrop"
  install -Dm755 "${startdir}/userspace/omdrop-discoverable" "${lib}/omdrop-discoverable"
  install -Dm755 "${startdir}/userspace/awdl-up" "${lib}/awdl-up"
  local helper
  for helper in awdl-airdrop-adv awdl-election awdl-mdns-respond awdl-peer-watch \
                awdl-peers awdl-stats ble-airdrop-adv brcm_iovar mkpsf \
                awdl-af-parse airdrop-send; do
    install -Dm755 "${startdir}/userspace/${helper}.py" "${lib}/${helper}.py"
  done
  # Sending. Proven 2026-09-18 to a Mac and an iPhone: dial the peer's EUI-64
  # link-local on the port it advertises, rather than waiting for an mDNS
  # advert a Mac never publishes. Neither needs root -- they read the peer
  # table through the helper above.
  install -Dm755 "${startdir}/userspace/send-to-peer" "${lib}/send-to-peer"
  install -Dm755 "${startdir}/userspace/awdl-resolve" "${lib}/awdl-resolve"
  # Imported, never executed: the machine identity and tunables every helper
  # above reads instead of carrying a baked-in MAC or hostname.
  install -Dm644 "${startdir}/userspace/awdl_identity.py" "${lib}/awdl_identity.py"

  install -Dm644 "${startdir}/packaging/org.omarchy.omdrop.policy" \
    "${pkgdir}/usr/share/polkit-1/actions/org.omarchy.omdrop.policy"
  install -Dm644 "${startdir}/packaging/awdl0.service" \
    "${pkgdir}/usr/lib/systemd/system/awdl0.service"
  # Vendor directories, not /etc: both are read after the package's own copy is
  # in place, and both leave an /etc override to the person running the machine.
  install -Dm644 "${startdir}/packaging/brcmfmac-awdl.conf" \
    "${pkgdir}/usr/lib/modprobe.d/brcmfmac-awdl.conf"
  install -Dm644 "${startdir}/packaging/99-awdl-unmanaged.conf" \
    "${pkgdir}/usr/lib/NetworkManager/conf.d/99-awdl-unmanaged.conf"

  install -Dm644 "${startdir}/README.md" "${pkgdir}/usr/share/doc/${pkgname}/README.md"
  # Documentation, deliberately not /etc/systemd/network: renaming the Wi-Fi
  # interface breaks any NetworkManager profile pinned to the old name, and
  # nothing this package installs needs the rename.
  install -Dm644 "${startdir}/packaging/10-wld0.link" \
    "${pkgdir}/usr/share/doc/${pkgname}/10-wld0.link"
}
