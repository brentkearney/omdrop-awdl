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
pkgver=0.1.0
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
makedepends=('git')
optdepends=('linux-asahi-headers: build against the Asahi kernel'
            'networkmanager: keeps awdl0 unmanaged and settles the Wi-Fi MAC before awdl0 is derived from it'
            'python-opendrop: the AirDrop receiver the omdrop plugin runs on top of this'
            'bluez: the opt-in BLE Continuity advert'
            'python-dbus: the opt-in BLE Continuity advert'
            'python-gobject: the opt-in BLE Continuity advert')
install="${pkgname}.install"

# Pinned deliberately. The patches were developed against this tag; a newer
# Asahi tree may need them rebased, and silently building against whatever is
# current would turn a rebase conflict into a runtime surprise.
source=("linux-asahi::git+https://github.com/AsahiLinux/linux.git#tag=${_asahitag}"
        'dkms.conf.in'
        'patches/'
        'userspace/'
        'packaging/')
sha256sums=('SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP')

prepare() {
  cd "${srcdir}/linux-asahi"
  for p in "${startdir}"/patches/*.patch; do
    echo "applying ${p##*/}"
    git apply "$p"
  done
}

package() {
  local src="${srcdir}/linux-asahi/drivers/net/wireless/broadcom/brcm80211"
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
                awdl-peers awdl-stats ble-airdrop-adv brcm_iovar mkpsf; do
    install -Dm755 "${startdir}/userspace/${helper}.py" "${lib}/${helper}.py"
  done
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
