#!/usr/bin/env python3
"""Send one file over AirDrop from awdl0 to a Mac (the f0010 -> hume direction).

Browse `_airdrop._tcp` on awdl0, pick the receiver named by --to (substring of
its Discover name or its 12-hex service id; first discoverable one if absent),
then Discover -> Ask -> Upload on one TLS connection, as OpenDrop's client does.
The Mac must be receiving (Finder > AirDrop open, or woken by BLE); it answers
/Discover only in that state, and /Ask blocks until its user accepts.

The peer must already be registered in our firmware (awdl-peer-watch.py does
it on the Mac's first frame) or every unicast we send is tossed (0x0003).

Exit: 0 sent | 2 no receiver found in --timeout | 3 declined | 4 upload failed.
Every step is timestamped to stdout so it lines up with the hold's intervals.
"""
import argparse
import ipaddress
import logging
import os
import pwd
import sys
import threading
import time

import opendrop.client as od_client
from opendrop.client import AirDropBrowser, AirDropClient
from opendrop.config import AirDropConfig, AirDropReceiverFlags


# Every AWDL HTTPS connection (Discover, Ask, Upload) is built through the
# subclass below, so this is the one place a default operation timeout belongs.
# It is NOT socket.setdefaulttimeout(): that would also arm zeroconf's browser
# sockets. Reassigned from --op-timeout before any connection is opened.
#
# 021922Z: our third-leg ACK and TLS Client Hello were emitted and lost, hume
# retransmitted SYN-ACK 13 times, and TCP backed off to rto:120000 (backoff:8).
# With timeout None the client sat in that backoff for 4.5 minutes -- past the
# end of a watched hold -- instead of failing and reporting. A wedged send must
# cost seconds, not a hold.
OP_TIMEOUT = 20.0

class HTTPSConnectionAWDL(od_client.HTTPSConnectionAWDL):
    """OpenDrop passes key_file/cert_file to HTTPSConnection.__init__, which
    Python 3.12 removed; the certificate is already in the SSL context."""
    def __init__(self, host, port=None, key_file=None, cert_file=None, timeout=None,
                 source_address=None, *, context=None, check_hostname=None, interface_name=None):
        import ipaddress, socket
        from http.client import HTTPSConnection
        # A zone index only means something for link-local; "::1%lo" does not resolve.
        if interface_name is not None and '%' not in host:
            ip = ipaddress.ip_address(host)
            if isinstance(ip, ipaddress.IPv6Address) and ip.is_link_local:
                host = host + '%' + interface_name
        if timeout is None:
            timeout = OP_TIMEOUT if OP_TIMEOUT else socket.getdefaulttimeout()
        HTTPSConnection.__init__(self, host=host, port=port, timeout=timeout,
                                 source_address=source_address, context=context)
        self.interface_name = interface_name
        self._create_connection = self.create_connection_awdl


od_client.HTTPSConnectionAWDL = HTTPSConnectionAWDL

# OpenDrop's icon generator uses PIL.Image.ANTIALIAS, removed in Pillow 10.
import PIL.Image
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

ap = argparse.ArgumentParser()
# Optional, because --discover-only asks a peer for its name and sends nothing.
ap.add_argument('file', nargs='?')
ap.add_argument('--iface', default='awdl0')
ap.add_argument('--discover-only', action='store_true',
                help='ask the peer for its name over /Discover, print it, and send nothing')
ap.add_argument('--to', default=None, help='receiver: substring of its name, or 12-hex id')
ap.add_argument('--timeout', type=float, default=60, help='seconds to wait for a receiver')
ap.add_argument('--ask-timeout', type=float, default=90, help='seconds for the Mac user to accept')
ap.add_argument('--op-timeout', type=float, default=20,
                help='seconds any single Discover/Upload socket operation may block; '
                     'guards against a wedged AWDL TX path parking the client in TCP backoff')
ap.add_argument('--af-attempts', type=int, default=1, metavar='N',
                help='independent Discover attempts against the freshest matching af.log '
                     'endpoint, re-read before each; stops on the first success. Total time '
                     'is bounded by N*(op-timeout+2)')
ap.add_argument('--auth-tag', default=None, help='3 bytes hex: SenderIdentityAuthTag for the Ask (the bytes our BLE advert carries)')
ap.add_argument('--should-convert', action='store_true',
                help="set Files[].ShouldConvertMediaFormats (a real sender's key; off = one variable held)")
ap.add_argument('--direct', default=None, metavar='HOST:PORT',
                help='skip discovery; Discover/Ask/Upload this endpoint (e.g. "[::1]:8771" against a local airdrop-serve)')
ap.add_argument('--af-log', default=None,
                help='awdl-af-listen output; take the receiver from the newest AirDrop Service Response '
                     'TLVs in it (address = EUI-64 link-local of the frame source) before browsing mDNS')
ap.add_argument('--name', default='f0010', help='SenderComputerName shown in the Mac dialog')
ap.add_argument('--model', default='MacBookPro18,3', help='SenderModelName')
ap.add_argument('--host', default='f0010-awdl')
ap.add_argument('--keys', default=os.path.join(pwd.getpwuid(os.getuid()).pw_dir, '.opendrop'))
args = ap.parse_args()
OP_TIMEOUT = args.op_timeout
if not args.discover_only and not args.file:
    ap.error('a file is required unless --discover-only')
if args.discover_only and not args.direct:
    ap.error('--discover-only needs --direct HOST:PORT, the peer to ask')
# A name lookup is not a transfer: keep the debug stream off stdout so the
# caller can read the name without parsing a log.
if args.discover_only:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

logging.basicConfig(level=logging.DEBUG, stream=sys.stdout,
                    format='%(asctime)s %(levelname)s %(name)s: %(message)s')
logging.getLogger('zeroconf').setLevel(logging.INFO)
log = logging.getLogger('send')

with open(f'/sys/class/net/{args.iface}/address') as f:
    sid = f.read().strip().replace(':', '')

config = AirDropConfig(host_name=args.host, computer_name=args.name,
                       computer_model=args.model, airdrop_dir=args.keys,
                       service_id=sid, interface=args.iface, debug=True)

T0 = time.monotonic()
def t():
    return f'+{time.monotonic() - T0:6.2f}s'


def send_ask_modern(self, file_path, is_url=False, icon=None):
    """OpenDrop's /Ask body predates today's sharingd. A Mac's Ask (captured by
    our receiver, 2026-09-07) carries TransferID, TransferType, Items, per-file
    FileSize, a UTI such as public.jpeg, and a ~25 KB icon; an iPhone answered
    OpenDrop's shape with 200 Discover and then showed no UI for the Ask."""
    import plistlib, uuid, io, mimetypes
    from PIL import Image
    files = [file_path] if isinstance(file_path, str) else list(file_path)
    uti = {'.jpg': 'public.jpeg', '.jpeg': 'public.jpeg', '.png': 'public.png', '.heic': 'public.heic',
           '.mov': 'com.apple.quicktime-movie', '.mp4': 'public.mpeg-4', '.pdf': 'com.adobe.pdf',
           '.txt': 'public.plain-text'}
    entries = []
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        entries.append({
            'FileName': os.path.basename(f),
            'FileType': uti.get(ext, 'public.data'),
            'FileSize': os.path.getsize(f),
            'FileBomPath': os.path.join('.', os.path.basename(f)),
            'FileIsDirectory': False,
            'ConvertMediaFormats': False,
        })
    # One TransferID for Ask and Upload: sharingd sends the same UUID in the Ask
    # body and the Upload's TransferID header (hume, 093540Z), and our receiver
    # files the upload under it.
    self.transfer_id = str(uuid.uuid4()).upper()
    if args.should_convert:
        for e in entries:
            e['ShouldConvertMediaFormats'] = False
    body = {
        'TransferID': {'id': self.transfer_id},
        'TransferType': {'files': {}},
        'SenderID': self.config.service_id,
        'BundleID': 'com.apple.finder',
        'SenderComputerName': self.config.computer_name,
        'SenderModelName': self.config.computer_model,
        'Items': [],
        'Files': entries,
        'ConvertMediaFormats': False,
    }
    if self.config.record_data:
        body['SenderRecordData'] = self.config.record_data
    # 09-08: hume's own Ask carries a 3-byte SenderIdentityAuthTag and its log
    # binds the HTTP sender to a BLE presence ("set the auth tag") right before
    # HELLO/ASK. First guess for the binding: the 3 bytes after 0x40 in the
    # sender's BLE AirDrop advert (ble-airdrop-adv.py --tag). --auth-tag sets it.
    if args.auth_tag:
        body['SenderIdentityAuthTag'] = bytes.fromhex(args.auth_tag)
    try:
        im = Image.open(files[0]); im.thumbnail((240, 240)); buf = io.BytesIO()
        im.convert('RGB').save(buf, 'JPEG', quality=80); body['FileIcon'] = buf.getvalue()
    except Exception:
        pass
    ok, resp = self.send_POST('/Ask', plistlib.dumps(body, fmt=plistlib.FMT_BINARY))
    log.info('%s ASK response %d B: %s', t(), len(resp), resp[:120])
    return ok


AirDropClient.send_ask = send_ask_modern


def send_discover_modern(self):
    """A Mac's /Discover request carries DeviceSupportFlags (and its record
    data); OpenDrop sends an empty plist. 22:13Z: an Apple-shaped Ask after an
    empty Discover still produced no UI on an iPhone."""
    import plistlib
    body = {'DeviceSupportFlags': 111611}
    if self.config.record_data:
        body['SenderRecordData'] = self.config.record_data
    _, resp = self.send_POST('/Discover', plistlib.dumps(body, fmt=plistlib.FMT_BINARY))
    pl = plistlib.loads(resp)
    log.info('%s DISCOVER response keys: %s', t(), sorted(pl))
    return pl.get('ReceiverComputerName')


AirDropClient.send_discover = send_discover_modern


def dvzip_encode(data, block=1 << 16):
    """Inverse of airdrop-serve.py's dvzip_decode: 4-byte big-endian header per
    block, low 31 bits = payload length, high bit = stored. A block is stored
    when zlib does not shrink it (sharingd does this for incompressible media:
    215526Z, a 22 MB video was 20 zlib blocks then 151 stored ones)."""
    import zlib
    out = bytearray()
    for i in range(0, len(data), block):
        raw = data[i:i + block]
        z = zlib.compress(raw, 6)
        if len(z) < len(raw):
            out += len(z).to_bytes(4, 'big') + z
        else:
            out += (0x80000000 | len(raw)).to_bytes(4, 'big') + raw
    return bytes(out)


def send_upload_modern(self, file_path, is_url=False):
    """sharingd's Upload, as our receiver saw hume's (093540Z):
         Content-Type: application/x-dvzip   TotalBytes: <sum of file sizes>
         TransferID: <the Ask's UUID>        Transfer-Encoding: chunked
    body = DVZip-framed cpio archive of the files. OpenDrop's default is
    application/x-cpio + gzip, which sharingd rejects (406 seen 09-07 the other
    way round). Same TLS connection as the Ask (send_POST reuses http_conn).
    The archive is written with libarchive directly: OpenDrop's AbsArchiveWrite
    constructs ArchiveEntry(None, entry_p), which newer python-libarchive-c
    reads as header_codec=<pointer> and fails on the first pathname."""
    import io, libarchive
    if is_url:
        return True
    files = [file_path] if isinstance(file_path, str) else list(file_path)
    stream = io.BytesIO()
    cwd = os.getcwd()
    with libarchive.custom_writer(stream.write, 'cpio') as archive:
        for f in files:
            # Store as "./<basename>", as sharingd does, whatever the source path.
            d, b = os.path.split(os.path.abspath(f))
            os.chdir(d)
            try:
                archive.add_files(b)
            finally:
                os.chdir(cwd)
    body = dvzip_encode(stream.getvalue())
    headers = {
        'Content-Type': 'application/x-dvzip',
        'TotalBytes': str(sum(os.path.getsize(f) for f in files)),
        'TransferID': getattr(self, 'transfer_id', None) or str(__import__('uuid').uuid4()).upper(),
    }
    log.info('%s UPLOAD %d B dvzip (%d B cpio) TransferID %s', t(), len(body), stream.getbuffer().nbytes, headers['TransferID'])
    ok, _ = self.send_POST('/Upload', io.BytesIO(body), headers=headers)
    return ok


AirDropClient.send_upload = send_upload_modern

found = threading.Event()
chosen = {}
lock = threading.Lock()


def on_add(info):
    """zeroconf found a `_airdrop._tcp` instance; Discover it in this thread."""
    if info is None or found.is_set():
        return
    ident = info.name.split('.')[0]
    addrs = info.parsed_addresses()
    props = {k.decode(): v.decode(errors='replace') for k, v in (info.properties or {}).items() if v is not None}
    log.info('%s advert %s host=%s port=%s addr=%s flags=%s', t(), ident, info.server, info.port,
             addrs, props.get('flags'))
    if not addrs:
        return
    if args.to and len(args.to) == 12 and args.to.lower() != ident.lower():
        return
    # --to as a MAC: only that device (000827Z: the browse Discovered f0011 while
    # the target was hume). Its AWDL link-local is the EUI-64 of the MAC.
    if args.to and ':' in args.to and ipaddress.IPv6Address(eui64_linklocal(args.to)) not in [ipaddress.IPv6Address(a.split('%')[0]) for a in addrs]:
        log.info('%s %s at %s is not --to %s; skipping', t(), ident, addrs, args.to)
        return
    flags = int(props.get('flags', AirDropReceiverFlags.SUPPORTS_DISCOVER_MAYBE))
    if not flags & AirDropReceiverFlags.SUPPORTS_DISCOVER_MAYBE:
        log.info('%s %s: no /Discover support (flags %#x); skipping', t(), ident, flags)
        return
    client = AirDropClient(config, (addrs[0], int(info.port)))
    client.http_conn = None
    try:
        t1 = time.monotonic()
        name = client.send_discover()
        log.info('%s DISCOVER %s -> %r (%.2fs)', t(), ident, name, time.monotonic() - t1)
    except Exception as e:  # timeouts, resets, TLS: all mean "not this one, not now"
        log.info('%s DISCOVER %s failed: %r', t(), ident, e)
        return
    if name is None:
        return
    if args.to and len(args.to) != 12 and args.to.lower() not in name.lower():
        log.info('%s %s is %r, not --to %r; skipping', t(), ident, name, args.to)
        return
    with lock:
        if found.is_set():
            return
        chosen.update(ident=ident, name=name, client=client, addr=addrs[0], port=int(info.port))
        found.set()


def eui64_linklocal(mac):
    b = bytes(int(x, 16) for x in mac.split(':'))
    b = bytes([b[0] ^ 0x02]) + b[1:3] + b'\xff\xfe' + b[3:]
    return 'fe80::' + ':'.join(f'{b[i] << 8 | b[i + 1]:x}' for i in range(0, 8, 2))


def endpoints_from_af_log(path):
    """Newest AirDrop endpoint per source MAC from awdl-af-listen output."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import importlib
    afp = importlib.import_module('awdl-af-parse')
    eps = {}
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                rec = afp.parse_body(bytes.fromhex(parts[3]))
            except ValueError:
                continue
            ep = afp.airdrop_endpoint(rec) if rec else None
            if ep and ep['port']:
                eps[parts[2]] = dict(ep, ts=float(parts[0]), sa=parts[2])
    return sorted(eps.values(), key=lambda e: -e['ts'])


if args.direct:
    # Offline / loopback test path: no discovery, the endpoint is given.
    host, _, port = args.direct.rpartition(':')
    host = host.strip('[]')
    client = AirDropClient(config, (host, int(port)))
    client.http_conn = None
    try:
        t1 = time.monotonic()
        name = client.send_discover()
        log.info('%s DISCOVER %s -> %r (%.2fs)', t(), args.direct, name, time.monotonic() - t1)
    except Exception as e:
        log.info('%s DISCOVER %s failed: %r', t(), args.direct, e)
        # --discover-only silences the debug stream, so without this the
        # failure is invisible: no name, no reason, exit 2.
        if args.discover_only:
            print(f'{args.direct}: no answer ({type(e).__name__})', file=sys.stderr)
        sys.exit(2)
    if args.discover_only:
        # A peer that answers without naming itself is not an error: an iPhone
        # answers /Discover with no ReceiverComputerName unless it recognizes
        # the sender. Print nothing and exit 0, so the caller distinguishes
        # "no name offered" from "peer did not answer" (exit 2 above).
        if name:
            print(name)
        sys.exit(0)
    chosen.update(ident='direct', name=name, client=client, addr=host, port=int(port))
    found.set()

if not found.is_set() and args.af_log and os.path.exists(args.af_log):
    def freshest_af_endpoint():
        """Newest af.log endpoint matching --to, re-read before EVERY attempt so a
        rotated service id, port or host between tries is picked up rather than a
        stale one retried. --to may be a 12-hex service id (rotates per session on
        iOS), a MAC (stable for the AWDL session), or a name substring (checked
        after Discover)."""
        for ep in endpoints_from_af_log(args.af_log):
            if args.to and len(args.to) == 12 and args.to.lower() != ep['id'].lower():
                continue
            if args.to and ':' in args.to and args.to.lower() != ep['sa'].lower():
                continue
            return ep
        return None

    # 033602Z: one Discover attempt sent 9 SYNs over 20 s and drew zero SYN-ACKs,
    # while 021922Z's SYN did arrive -- the TX loss is probabilistic, so a single
    # attempt per hold measures nothing. N independent attempts turn it into a
    # rate. Bounded overall so a run can never outlive its hold.
    attempts = max(1, args.af_attempts)
    deadline = time.monotonic() + attempts * (args.op_timeout + 2)
    tried = 0
    for attempt in range(1, attempts + 1):
        if time.monotonic() >= deadline:
            log.info('%s AF attempts: time budget spent after %d/%d', t(), tried, attempts)
            break
        ep = freshest_af_endpoint()
        if ep is None:
            log.info('%s AF attempt %d/%d: no matching endpoint in %s', t(), attempt, attempts, args.af_log)
            break
        tried = attempt
        addr = eui64_linklocal(ep['sa'])
        log.info('%s AF attempt %d/%d endpoint %s from %s: [%s]:%d host=%s txt=%s (%.0fs old)',
                 t(), attempt, attempts, ep['id'], ep['sa'], addr, ep['port'], ep['host'], ep['txt'],
                 time.time() - ep['ts'])
        client = AirDropClient(config, (addr, ep['port']))
        t1 = time.monotonic()
        try:
            name = client.send_discover()
            log.info('%s AF attempt %d/%d DISCOVER %s -> %r (%.2fs)', t(), attempt, attempts,
                     ep['id'], name, time.monotonic() - t1)
        except Exception as e:
            log.info('%s AF attempt %d/%d DISCOVER %s failed: %r (%.2fs)', t(), attempt, attempts,
                     ep['id'], e, time.monotonic() - t1)
            continue
        if name is None or (args.to and len(args.to) != 12 and ':' not in args.to and args.to.lower() not in name.lower()):
            continue
        chosen.update(ident=ep['id'], name=name, client=client, addr=addr, port=ep['port'])
        found.set()
        break
    if not found.is_set():
        log.info('%s no usable AirDrop endpoint in %s after %d attempt(s); browsing mDNS',
                 t(), args.af_log, tried)

if not found.is_set():
    browser = AirDropBrowser(config)
    log.info('%s browsing _airdrop._tcp on %s from %s as %s (%s)', t(), args.iface, browser.ip_addr, sid, args.name)
    browser.start(callback_add=on_add)
    found.wait(args.timeout)
    browser.stop()
if not found.is_set():
    log.info('%s no receiver found in %.0fs', t(), args.timeout)
    sys.exit(2)

client = chosen['client']
log.info('%s FOUND %s %r at [%s]:%d; asking', t(), chosen['ident'], chosen['name'], chosen['addr'], chosen['port'])
# BEGIN ask-connection
# sharingd tears the /Discover handler down ~1 ms after answering it (hume's
# log, 09-09 04:37:41.059Z: "Tearing down handler for server" / "Removed
# handler for discovery bonjour connection"). The socket stays open at TCP
# level with nothing above TCP ever reading it again, so requeue-2's Ask was
# ACKed in full -- all 15,127 bytes, Ack=15127 -- and never dispatched, then
# timed out at +91 s. A real sender throws the Discover connection away. Do it
# unconditionally, on every discovery path: the old `if http_conn is None`
# guard only fired for --direct, which was the one path that cleared it, so
# mDNS and AF-endpoint sends silently reused a connection already dismantled.
# The Ask also blocks on the receiver's consent prompt, so it carries the long
# timeout.
if client.http_conn is not None:
    try:
        client.http_conn.close()
    except OSError:
        pass
client.http_conn = HTTPSConnectionAWDL(client.receiver_host, client.receiver_port,
                                       interface_name=config.interface, context=config.get_ssl_context())
client.http_conn.connect()
client.http_conn.sock.settimeout(args.ask_timeout)
# END ask-connection
t1 = time.monotonic()
try:
    ok = client.send_ask(args.file)
except Exception as e:
    log.info('%s ASK failed: %r', t(), e)
    sys.exit(3)
log.info('%s ASK -> %s (%.2fs)', t(), 'accepted' if ok else 'declined', time.monotonic() - t1)
if not ok:
    sys.exit(3)
t1 = time.monotonic()
size = os.path.getsize(args.file)
try:
    ok = client.send_upload(args.file)
except Exception as e:
    log.info('%s UPLOAD failed: %r', t(), e)
    sys.exit(4)
log.info('%s UPLOAD %s -> %s (%d B, %.2fs)', t(), os.path.basename(args.file), 'ok' if ok else 'FAILED', size,
         time.monotonic() - t1)
sys.exit(0 if ok else 4)
