import json
import ipaddress
import socket
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import paramiko

from discovery import PROTOCOL, discover, identity, is_lan_address, key_fingerprint, pairing_code, parse_reply, targets
import test_transfer
from transfer_core import Phone


FALLBACK_HOST = '198.51.100.2'  # RFC 5737 fixture only.


class DiscoveryTests(unittest.TestCase):
    def test_reply_rejects_wrong_nonce_protocol_and_unbounded_input(self):
        valid = {'protocol': PROTOCOL, 'nonce': 'x', 'fingerprint': 'SHA256:' + 'A' * 43,
                 'paired': True, 'port': 52022}
        self.assertIsNotNone(parse_reply(json.dumps(valid).encode(), 'x', '127.0.0.1'))
        for changed in [{'nonce': 'y'}, {'protocol': 'other'}, {'port': 70000},
                        {'paired': 'yes'}, {'fingerprint': 'SHA256:bad'}]:
            self.assertIsNone(parse_reply(json.dumps(valid | changed).encode(), 'x', '127.0.0.1'))
        self.assertIsNone(parse_reply(b'x' * 4097, 'x', '127.0.0.1'))
        self.assertIsNone(parse_reply(b'[]', 'x', '127.0.0.1'))
        self.assertEqual(targets(FALLBACK_HOST, [], fallback_host=FALLBACK_HOST), [FALLBACK_HOST])
        self.assertEqual(pairing_code('phone', 'computer'), '761865')

    def test_lan_uses_actual_netmask_and_interface_bound_broadcast(self):
        interfaces = [ipaddress.IPv4Interface('192.0.2.251/23')]
        self.assertTrue(is_lan_address('192.0.3.100', interfaces))
        self.assertFalse(is_lan_address(FALLBACK_HOST, interfaces))
        self.assertFalse(is_lan_address('192.0.2.251', interfaces))
        self.assertFalse(is_lan_address('192.0.4.1', interfaces))
        self.assertEqual(targets(FALLBACK_HOST, interfaces, fallback_host=FALLBACK_HOST),
                         ['192.0.3.255', '255.255.255.255', FALLBACK_HOST])
        sent = []
        class Probe:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def setsockopt(self, *args): pass
            def setblocking(self, *args): pass
            def bind(self, source): self.source = source[0]
            def sendto(self, data, address): sent.append((self.source, address[0]))
        key = paramiko.ECDSAKey.generate()
        with patch('discovery.lan_interfaces', return_value=interfaces), patch('discovery.socket.socket', side_effect=lambda *a: Probe()):
            discover(FALLBACK_HOST, key, timeout=0, fallback_host=FALLBACK_HOST)
        self.assertIn(('192.0.2.251', '192.0.3.255'), sent)
        self.assertIn(('', FALLBACK_HOST), sent)
        sent.clear()
        with patch('discovery.lan_interfaces', return_value=interfaces), patch('discovery.socket.socket', side_effect=lambda *a: Probe()):
            discover(FALLBACK_HOST, key, timeout=0, lan_only=True, fallback_host=FALLBACK_HOST)
        self.assertNotIn(('', FALLBACK_HOST), sent)

    def test_lan_reply_wins_over_saved_overlay_with_same_phone_key(self):
        key = paramiko.ECDSAKey.generate()
        errors = []
        servers = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(2)]
        threads = []
        try:
            servers[0].bind(('127.0.0.1', 0))
            port = servers[0].getsockname()[1]
            servers[1].bind(('127.0.0.2', port))
            def respond(server):
                try:
                    server.settimeout(2)
                    data, sender = server.recvfrom(4096)
                    query = json.loads(data)
                    server.sendto(json.dumps({'protocol': PROTOCOL, 'nonce': query['nonce'],
                        'fingerprint': 'SHA256:' + 'A' * 43, 'paired': True, 'port': 52022}).encode(), sender)
                except Exception as ex:
                    errors.append(ex)
            for server in servers:
                thread = threading.Thread(target=respond, args=(server,))
                thread.start()
                threads.append(thread)
            with patch('discovery.lan_interfaces', return_value=[ipaddress.IPv4Interface('127.0.0.1/24')]):
                found = discover('127.0.0.1', key, .3, ['127.0.0.1', '127.0.0.2'], port)
            self.assertEqual([phone['host'] for phone in found], ['127.0.0.2', '127.0.0.1'])
            self.assertEqual(found[0]['route'], 'lan')
        finally:
            for thread in threads: thread.join(3)
            for server in servers: server.close()
        self.assertFalse(errors)

    def test_udp_discovery_uses_nonce_public_key_and_no_secret(self):
        key = paramiko.ECDSAKey.generate()
        errors = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            server.bind(('127.0.0.1', 0))
            server.settimeout(2)
            def respond():
                try:
                    data, sender = server.recvfrom(4096)
                    request = json.loads(data)
                    self.assertEqual(set(request), {'protocol', 'nonce', 'name', 'key'})
                    self.assertEqual(request['key'], key.get_name() + ' ' + key.get_base64())
                    response = {'protocol': PROTOCOL, 'nonce': request['nonce'], 'name': '测试手机',
                                'fingerprint': 'SHA256:' + 'A' * 43, 'port': 52022, 'paired': True}
                    server.sendto(json.dumps(response).encode(), sender)
                except Exception as ex:
                    errors.append(ex)
            thread = threading.Thread(target=respond)
            thread.start()
            phones = discover('127.0.0.1', key, .3, ['127.0.0.1'], server.getsockname()[1])
            thread.join(3)
        self.assertFalse(errors)
        self.assertEqual(phones[0]['name'], '测试手机')
        self.assertEqual(phones[0]['host'], '127.0.0.1')

    def test_key_auth_device_directory_heartbeat_and_default_upload(self):
        fixture = test_transfer.TransferTests('test_thumbnail_and_names')
        test_transfer.TransferTests.setUpClass()
        fixture.setUp()
        try:
            key = identity()
            self.assertEqual(key_fingerprint(key), key_fingerprint(identity()))
            fixture.public_key = key
            receiver = fixture.remote / 'Download' / '互传'
            receiver.mkdir(parents=True)
            state = fixture.remote / '.lc-transfer'
            state.mkdir()
            (state / 'device.json').write_text(json.dumps({'receive_dir': '/Download/互传'}))
            settings = replace(fixture.settings, key_auth=True)
            source = fixture.base / '电脑传来的.zip'
            source.write_bytes(b'archive contents')
            with Phone(settings, '') as phone:
                self.assertEqual(phone.device_config(), '/Download/互传')
                phone.heartbeat()
                self.assertTrue((state / 'session.json').exists())
                self.assertEqual(phone.upload([source], phone.device_config()), 1)
                self.assertNotIn('.lc-transfer', {entry.name for entry in phone.list_dir('/')[1]})
                self.assertEqual((receiver / source.name).read_bytes(), source.read_bytes())
                (state / 'device.json').write_text(json.dumps({'receive_dir': '/outside'}))
                with self.assertRaises(ValueError):
                    phone.device_config()
            fixture.public_key = paramiko.ECDSAKey.generate()
            with self.assertRaises(paramiko.AuthenticationException):
                with Phone(settings, ''):
                    pass
        finally:
            fixture.tearDown()

    def test_unreachable_address_does_not_abort_other_discovery_replies(self):
        key = paramiko.ECDSAKey.generate()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            server.bind(('127.0.0.1', 0))
            server.settimeout(2)
            def respond():
                request, sender = server.recvfrom(2048)
                query = json.loads(request)
                time.sleep(.05)  # The closed address reports ICMP before the phone responds.
                reply = {'protocol': PROTOCOL, 'nonce': query['nonce'], 'paired': True,
                         'name': '可达的手机', 'port': 52022, 'fingerprint': 'SHA256:' + 'A' * 43}
                server.sendto(json.dumps(reply).encode(), sender)
            thread = threading.Thread(target=respond)
            thread.start()
            try:
                found = discover('127.0.0.1', key, .4, ['127.0.0.2', '127.0.0.1'], server.getsockname()[1])
            finally:
                thread.join(3)
        self.assertEqual([phone['name'] for phone in found], ['可达的手机'])
