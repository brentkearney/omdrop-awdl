# Publishing this package to the AUR

The AUR is a git-hosted recipe index: what gets pushed is a `PKGBUILD` and a `.SRCINFO`, never a built package. Users' helpers clone the recipe and build locally.

`aur/PKGBUILD` and `aur/.SRCINFO` are **generated** by `aur/prepare.sh` from the `PKGBUILD` in the repository root. Edit the root one; re-run the script. Two hand-maintained recipes for one package is how a project ships code nobody tested — the root recipe is the one `omdrop install-driver` runs, so it is the one that gets exercised.

The two differ in exactly one respect: an AUR checkout contains only what is pushed to it, so the generated recipe fetches the release tarball at the `v<pkgver>` tag and reads `patches/`, `userspace/` and `packaging/` out of it, where the root recipe reads them from the clone it sits in.

## Before the first push

- **An AUR account with an SSH public key registered** at `aur.archlinux.org`. Pushes go to `ssh://aur@aur.archlinux.org/brcmfmac-awdl-dkms.git`, and the repository is created by the first push.
- **The release tag must exist on GitHub.** The recipe downloads `$url/archive/v$pkgver.tar.gz`; until that tag is pushed there is nothing to download and nothing to checksum.

## Cutting a release

```bash
git tag -a v0.1.0 -m "..."      # if not already tagged
git push origin main --tags     # the tarball URL is the tag
./aur/prepare.sh --checksums    # fetches the tarball, writes the real sha256, regenerates .SRCINFO
```

`--checksums` needs `pacman-contrib` for `updpkgsums`. Without it the recipe carries `SKIP`, which publishes a package that downloads whatever the URL happens to serve — acceptable while nothing is published, not acceptable once it is.

Then prove it builds somewhere that is not this machine:

```bash
extra-aarch64-build              # or the aarch64 clean-chroot equivalent available to you
```

A clean chroot is where DKMS and Asahi surprises live: the root recipe has always been built on a machine that already had the kernel headers, the helpers' interpreters and a working AWDL setup.

## Pushing

```bash
git clone ssh://aur@aur.archlinux.org/brcmfmac-awdl-dkms.git /tmp/aur-brcmfmac-awdl-dkms
cp aur/PKGBUILD aur/.SRCINFO aur/brcmfmac-awdl-dkms.install /tmp/aur-brcmfmac-awdl-dkms/
cd /tmp/aur-brcmfmac-awdl-dkms && git add -A && git commit -m "brcmfmac-awdl-dkms 0.1.0" && git push
```

The AUR web interface parses `.SRCINFO` and nothing else. A push whose `.SRCINFO` disagrees with its `PKGBUILD` is the most common rejection, which is why `prepare.sh` always regenerates both together.

## Things known to draw comments

- **`opendrop` is an AUR package too**, and it pulls in `owlink` — a userspace AWDL daemon this package makes unnecessary. It is an `optdepends` here, not a dependency, so nobody is forced to install it, but the receiver does need it. There is no `python-opendrop`; that name does not exist.
- **This is an aarch64-only kernel module.** The AUR is `x86_64`-centric and Asahi users are served by the Asahi repositories, so the AUR may not be where the people who want this will look for it. Publishing through Asahi community channels is worth weighing against it rather than assuming the AUR is the destination.
- **The kernel tag is pinned** (`_asahitag`). That is deliberate — the patches were developed against it — but it means the package does not follow the newest Asahi tree, and someone will ask why.
