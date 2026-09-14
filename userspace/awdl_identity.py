"""Machine identity and tunables shared by the omdrop AWDL helpers.

Nothing here is baked in. The AWDL address, the infrastructure address and the
advertised hostname are properties of the machine this happens to run on, and
the channel knobs are properties of the room it is in: a helper that hardcodes
any of them ships one laptop's configuration to everybody. They are read from
the running system, and overridden per-machine from $OMDROP_CONF.
"""
import os
import socket

CONF = os.environ.get('OMDROP_CONF', '/etc/omdrop')
AWDL_IFACE = os.environ.get('OMDROP_AWDL_IFACE', 'awdl0')

# DNS labels are letters, digits and hyphen. A hostname is user-supplied text
# and goes on air inside an Arpa TLV, so anything else is dropped rather than
# encoded -- a label the peer cannot parse loses the whole service record.
_LABEL_OK = set('abcdefghijklmnopqrstuvwxyz0123456789-')


def knob(name, default):
    """One line of configuration from $OMDROP_CONF/<name>, or `default`."""
    try:
        with open(os.path.join(CONF, name)) as f:
            value = f.read().strip()
    except OSError:
        return default
    return value or default


def switch(name):
    """True when $OMDROP_CONF/<name> exists -- the diagnostic on/off files."""
    return os.path.exists(os.path.join(CONF, name))


def mac_of(iface):
    """The MAC of `iface` as six bytes."""
    with open(f'/sys/class/net/{iface}/address') as f:
        return bytes.fromhex(f.read().strip().replace(':', ''))


def infra_iface():
    """The Wi-Fi interface AWDL shares the radio with.

    Found rather than assumed: the interface is called wlan0 on a stock Arch
    install and wld0 on machines carrying a naming rule, and an AWDL setup that
    only works under one of those names is a setup that works on one laptop.
    Picks the first Broadcom FullMAC wireless netdev; override with
    $OMDROP_INFRA_IFACE or $OMDROP_CONF/infra-iface on a machine with two.
    """
    forced = os.environ.get('OMDROP_INFRA_IFACE') or knob('infra-iface', '')
    if forced:
        return forced
    for name in sorted(os.listdir('/sys/class/net')):
        if name == AWDL_IFACE:
            continue
        base = f'/sys/class/net/{name}'
        if not os.path.isdir(f'{base}/phy80211'):
            continue
        try:
            driver = os.path.basename(os.readlink(f'{base}/device/driver'))
        except OSError:
            continue
        if driver.startswith('brcmfmac'):
            return name
    return ''


def infra_mac():
    """The infrastructure MAC a PSF advertises, or six zero bytes.

    Zeros rather than an exception: the field is one TLV of a frame that is
    still worth sending, and refusing to build the frame at all because the
    STA interface went away mid-window is a worse failure than advertising an
    unset address.
    """
    iface = infra_iface()
    if iface:
        try:
            return mac_of(iface)
        except OSError:
            pass
    return b'\x00' * 6


def awdl_host():
    """The hostname this machine publishes over AWDL, without the .local.

    A Mac resolves the Arpa name out of our PSF and then queries mDNS for it,
    so the announcer, the responder and the PSF template must all agree; they
    agree by all calling this. Distinct from the system hostname because the
    name only ever resolves to an awdl0 link-local address.
    """
    forced = os.environ.get('OMDROP_AWDL_HOST') or knob('awdl-host', '')
    if forced:
        return forced.rstrip('.').removesuffix('.local')
    short = socket.gethostname().split('.')[0].lower()
    label = ''.join(c for c in short if c in _LABEL_OK).strip('-')
    return f'{label}-awdl' if label else 'omdrop-awdl'
