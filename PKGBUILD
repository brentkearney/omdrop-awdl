# Maintainer: Brent Kearney <1550934+brentkearney@users.noreply.github.com>
#
# AWDL support for the BCM4387 as a DKMS module.
#
# The patched brcmfmac installs to updates/dkms/, which depmod prefers over the
# in-tree module WITHOUT replacing it. That is the whole safety story: if this
# ever fails to build, the stock driver loads and Wi-Fi keeps working. You lose
# AWDL, never the network.

pkgname=brcmfmac-awdl-dkms
pkgver=0.1.0
pkgrel=1
_asahitag=asahi-7.1.13-2
pkgdesc="AWDL (AirDrop link layer) support for the BCM4387, as a DKMS module"
arch=('aarch64')
url="https://github.com/brentkearney/omdrop-awdl"
license=('GPL-2.0-only')
depends=('dkms')
makedepends=('git')
optdepends=('linux-asahi-headers: build against the Asahi kernel')
install="${pkgname}.install"

# Pinned deliberately. The patches were developed against this tag; a newer
# Asahi tree may need them rebased, and silently building against whatever is
# current would turn a rebase conflict into a runtime surprise.
source=("linux-asahi::git+https://github.com/AsahiLinux/linux.git#tag=${_asahitag}"
        'dkms.conf.in'
        'patches/')
sha256sums=('SKIP' 'SKIP' 'SKIP')

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

  install -Dm644 "${startdir}/README.md" "${pkgdir}/usr/share/doc/${pkgname}/README.md"
}
