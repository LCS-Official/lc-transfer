"""Small UDP discovery for the overlay and LAN; authentication stays in SSH."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import socket
import time
import select
from contextlib import ExitStack

import paramiko

from transfer_core import data_dir, key_fingerprint

PORT = 52023
PROTOCOL = 'lc-transfer/1'


def identity():
    path = data_dir() / 'computer-key.pem'
    if path.exists():
        return paramiko.ECDSAKey.from_private_key_file(str(path))
    key = paramiko.ECDSAKey.generate(bits=256)
    # AppData inherits this Windows user's access controls; never include it in releases.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
        key.write_private_key(output)
    return key


def pairing_code(phone_fingerprint, computer_fingerprint):
    digest = hashlib.sha256((phone_fingerprint + '\n' + computer_fingerprint).encode()).digest()
    return f'{int.from_bytes(digest[:4], "big") % 1000000:06d}'


def lan_interfaces():
    from PySide6.QtNetwork import QNetworkInterface
    result = set()
    for interface in QNetworkInterface.allInterfaces():
        flags = interface.flags()
        if (not flags & QNetworkInterface.InterfaceFlag.IsUp or
                not flags & QNetworkInterface.InterfaceFlag.IsRunning or
                interface.type() not in {QNetworkInterface.InterfaceType.Ethernet, QNetworkInterface.InterfaceType.Wifi}):
            continue
        name = (interface.humanReadableName() + ' ' + interface.name()).casefold()
        if any(word in name for word in ('vpn', 'easytier', 'wintun', 'wireguard', 'tailscale', 'zerotier',
                                         'virtual', 'vmware', 'hyper-v', 'wsl')):
            continue
        for entry in interface.addressEntries():
            try:
                address = ipaddress.IPv4Interface(f'{entry.ip().toString()}/{entry.prefixLength()}')
                if (not address.ip.is_loopback and not address.ip.is_link_local and
                        address.network.prefixlen < 31):
                    result.add(address)
            except ValueError:
                continue
    return sorted(result, key=lambda item: int(item.ip))


def is_lan_address(host, interfaces=None):
    try:
        address = ipaddress.IPv4Address(host)
        return any(address in local.network and address != local.ip
            for local in (lan_interfaces() if interfaces is None else interfaces))
    except ValueError:
        return False


def targets(host, interfaces=None, *, fallback_host=''):
    interfaces = lan_interfaces() if interfaces is None else interfaces
    addresses = [str(local.network.broadcast_address) for local in interfaces]
    if interfaces:
        addresses.append('255.255.255.255')
    if is_lan_address(host, interfaces):
        addresses.insert(0, host)
    if fallback_host:
        addresses.append(fallback_host)
    return list(dict.fromkeys(addresses))


def parse_reply(payload, nonce, address):
    if len(payload) > 4096:
        return None
    try:
        data = json.loads(payload)
        if data.get('protocol') != PROTOCOL or data.get('nonce') != nonce:
            return None
        if not isinstance(data.get('paired'), bool) or not isinstance(data.get('fingerprint'), str):
            return None
        if not data['fingerprint'].startswith('SHA256:') or len(data['fingerprint']) != 50:
            return None
        if not 1 <= int(data.get('port', 0)) <= 65535:
            return None
        data['host'] = str(ipaddress.IPv4Address(address))
        data['name'] = str(data.get('name', '安卓手机'))[:80]
        return data
    except (ValueError, TypeError, AttributeError, UnicodeError):
        return None


def discover(host, key, timeout=1.2, destinations=None, port=PORT, *, lan_only=False, fallback_host=''):
    nonce = secrets.token_hex(16)
    request = json.dumps({'protocol': PROTOCOL, 'nonce': nonce,
                          'name': socket.gethostname()[:80],
                          'key': key.get_name() + ' ' + key.get_base64()}).encode()
    found = {}
    interfaces = lan_interfaces()
    with ExitStack() as stack:
        sockets = []
        probes = [('', destinations)] if destinations is not None else []
        if destinations is None:
            for local in interfaces:
                addresses = [str(local.network.broadcast_address), '255.255.255.255']
                if is_lan_address(host, [local]):
                    addresses.append(host)
                probes.append((str(local.ip), addresses))
            if not lan_only and fallback_host:
                probes.append(('', [fallback_host]))
        for source, addresses in probes:
            try:
                sock = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.bind((source, 0))
                sock.setblocking(False)
            except OSError:
                continue
            sockets.append(sock)
            for address in dict.fromkeys(addresses):
                try:
                    sock.sendto(request, (address, port))
                except OSError:
                    continue
        deadline = time.monotonic() + timeout
        while sockets and time.monotonic() < deadline:
            ready, _, _ = select.select(sockets, [], [], max(.01, deadline - time.monotonic()))
            for sock in ready:
                try:
                    payload, sender = sock.recvfrom(4097)
                except (BlockingIOError, ConnectionResetError, ConnectionRefusedError):
                    # ICMP from another probe is not a failure of the reachable phone.
                    continue
                data = parse_reply(payload, nonce, sender[0])
                if data:
                    data['route'] = 'lan' if is_lan_address(data['host'], interfaces) else 'overlay'
                    found[(data['host'], data['fingerprint'])] = data
    return sorted(found.values(), key=lambda item: (item['route'] != 'lan', not item['paired'],
                  item['host'] != host, item['host'] != fallback_host, item['host']))
