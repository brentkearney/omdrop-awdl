#!/usr/bin/env bash
# Generate the recipe directory that omacom/omarchy-pkgs builds, from the
# PKGBUILD in this repository's root.
#
# Omarchy's package repository builds checked-in recipes on its own host and
# publishes signed binaries, so its copy of the recipe lives in THAT repository:
#
#     pkgbuilds/brcmfmac-awdl-dkms/
#       PKGBUILD                    <- generated here
#       brcmfmac-awdl-dkms.install  <- copied here
#       .omarchy/package.json       <- generated here
#
# That copy cannot read files next to it the way ours does; it downloads the
# release tarball. Rather than maintain a second recipe by hand, this derives
# it, so the root PKGBUILD stays the single source of truth for how the package
# is actually built.
#
# Two differences from ours, both required by the target:
#
#   - ${startdir} becomes the unpacked tarball inside ${srcdir}
#   - the linux-asahi-headers optdepend is dropped: Omarchy's base install
#     guarantees matching kernel headers before any DKMS package is installed,
#     and their PR 454 removed header dependencies from DKMS recipes for that
#     reason. Anyone building from this repository directly still sees the note
#     in our own PKGBUILD and README.
#
# Usage:
#     omarchy-pkgs/prepare.sh              regenerate the recipe (checksum SKIP)
#     omarchy-pkgs/prepare.sh --checksums  hash the pushed tag and fill it in
#
# Then copy omarchy-pkgs/pkgbuilds/ into a fork of omacom/omarchy-pkgs and open
# a pull request.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"
repo=$(basename "$root")
pkgname=$(awk -F= '/^pkgname=/ { print $2; exit }' "$root/PKGBUILD")
pkgver=$(awk -F= '/^pkgver=/ { print $2; exit }' "$root/PKGBUILD")
[[ -n $pkgname && -n $pkgver ]] || { echo "no pkgname/pkgver in $root/PKGBUILD" >&2; exit 1; }

dest="$here/pkgbuilds/$pkgname"
out="$dest/PKGBUILD"
mkdir -p "$dest/.omarchy"

# The AWDL patches are checked in beside the recipe, not left inside the
# tarball. omarchy-pkgs owns its recipes, and its README is explicit that
# "ordinary source-code patches still belong beside PKGBUILD and are applied by
# prepare() as needed" -- 222 patch files across that tree, yt6801-dkms and
# intel-ipu7-camera being the nearest shapes to this one. Patches nobody can see
# in the repository's own history cannot be reviewed or rebased there.
rm -f "$dest"/*.patch
cp "$root"/patches/*.patch "$dest/"
patch_names=()
patch_sums=()
for p in "$dest"/*.patch; do
  patch_names+=("$(basename "$p")")
  patch_sums+=("$(sha256sum "$p" | cut -d' ' -f1)")
done
[[ ${#patch_names[@]} -gt 0 ]] || { echo "no patches found in $root/patches" >&2; exit 1; }

{
  # No "generated, do not edit" banner. Every other recipe in that tree is
  # owned there, and a header claiming an external source of truth would be a
  # boundary dispute rather than a packaging detail.
  python3 - "$root/PKGBUILD" "$repo" "${patch_names[*]}" "${patch_sums[*]}" <<'PY'
import re, sys
src, repo = open(sys.argv[1]).read(), sys.argv[2]
names, sums = sys.argv[3].split(), sys.argv[4].split()

# The release tarball carries the vendored kernel directories; the patches sit
# beside the recipe. Everything is checksummed except the tarball, which gets
# its hash from --checksums once the tag is pushed.
entries = "\n        ".join(f'"{n}"' for n in names)
checksums = "\n            ".join(f"'{s}'" for s in sums)
src = re.sub(
    r"source=\((.*?)\)\nsha256sums=\((.*?)\)",
    f'source=("$pkgname-$pkgver.tar.gz::$url/archive/v$pkgver.tar.gz"\n        {entries})\n'
    "# The tarball's hash is filled in once its tag is pushed; the patches are\n"
    "# hashed here.\n"
    f"sha256sums=('SKIP'\n            {checksums})",
    src, flags=re.S)

# Everything that read a file beside the recipe now reads it out of the
# unpacked tarball, which GitHub names <repo>-<version>.
src = src.replace("${startdir}", "${srcdir}/" + repo + "-${pkgver}")
src = src.replace("${srcdir}/dkms.conf.in",
                  "${srcdir}/" + repo + "-${pkgver}/dkms.conf.in")

# The patch loop reads the copies beside the recipe, which makepkg stages into
# ${srcdir}, rather than the set inside the tarball. Same eleven patches; this
# is the copy omarchy-pkgs can review, diff and rebase in its own history. The
# glob sorts, so 0001 applies before 0002.
src, rewired = re.subn(
    r'for p in "\$\{srcdir\}/' + repo + r'-\$\{pkgver\}"/patches/\*\.patch; do',
    'for p in "${srcdir}"/[0-9][0-9][0-9][0-9]-*.patch; do',
    src)
if rewired != 1:
    sys.exit(f"expected one patch loop to rewire, found {rewired}")

# Omarchy's base install supplies matching kernel headers before DKMS packages
# are installed, and its recipes do not name them (their PR 454). Drop that one
# entry wherever it sits in the array, and re-emit the rest aligned.
def drop_headers(match):
    entries = [e for e in re.findall(r"'([^']*)'", match.group(1))
               if not e.startswith("linux-asahi-headers:")]
    joined = "\n            ".join(f"'{e}'" for e in entries)
    return f"optdepends=({joined})"

src, dropped = re.subn(r"optdepends=\((.*?)\)\n", lambda m: drop_headers(m) + "\n",
                       src, flags=re.S)
if dropped != 1:
    sys.exit(f"expected one optdepends array to rewrite, found {dropped}")
print(src, end="")
PY
} > "$out"

# The recipe's install hook has to sit beside it in the target repository.
install_file=$(awk -F= '/^install=/ { print $2; exit }' "$root/PKGBUILD")
install_file=${install_file//\"/}
install_file=${install_file//\$\{pkgname\}/$pkgname}
cp "$root/${install_file}" "$dest/${install_file}"

# Omarchy owns every checked-in recipe and follows upstream releases directly:
# a watch on this repository's tags is what keeps its copy current.
cat > "$dest/.omarchy/package.json" <<JSON
{
  "source": "local",
  "upstream": {
    "watch": {
      "github": "brentkearney/$repo",
      "pattern": "v(?P<version>[0-9]+(?:\\\\.[0-9]+)*)"
    }
  }
}
JSON

if [[ ${1:-} == --checksums ]]; then
  url=$(awk -F= '/^url=/ { gsub(/"/, "", $2); print $2; exit }' "$root/PKGBUILD")
  tarball="${url}/archive/v${pkgver}.tar.gz"
  echo "hashing ${tarball}"
  sum=$(curl -fsSL "$tarball" | sha256sum | cut -d' ' -f1)
  [[ ${#sum} -eq 64 ]] || { echo "could not hash ${tarball}" >&2; exit 1; }
  python3 - "$out" "$sum" <<'PY'
import re, sys
path, sum_ = sys.argv[1], sys.argv[2]
text = open(path).read()
text, count = re.subn(r"sha256sums=\('SKIP'", f"sha256sums=('{sum_}'", text)
if count != 1:
    sys.exit(f"expected one SKIP checksum to replace in {path}, found {count}")
open(path, "w").write(text)
PY
fi

echo "wrote $dest (pkgver $pkgver)"
# The tarball is the one source whose hash cannot be computed here, so an
# unreplaced SKIP means the build host would verify nothing about it.
if grep -q "sha256sums=('SKIP'" "$out"; then
  cat <<'MSG'

NOTE: the release tarball is still SKIP. Push the v<pkgver> tag to GitHub, then
      re-run with --checksums before opening the pull request.
MSG
fi
