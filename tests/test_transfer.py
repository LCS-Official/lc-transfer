"""Integration checks against an actual loopback SSH/SFTP server, no phone required."""
import errno
import hashlib
import os
import posixpath
import socket
import tempfile
import threading
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import paramiko

from transfer_core import Cancelled, Entry, Phone, Settings, UnknownHost, key_fingerprint, safe_name, validate_child


class Auth(paramiko.ServerInterface):
    def __init__(self, public_key=None, requests=None):
        self.public_key = public_key
        self.requests = requests if requests is not None else []

    def check_auth_publickey(self, username, key):
        return paramiko.AUTH_SUCCESSFUL if username == 'user' and self.public_key and key == self.public_key else paramiko.AUTH_FAILED

    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if (username, password) == ('user', 'test-password') else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return 'publickey,password'

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class Files(paramiko.SFTPServerInterface):
    def __init__(self, server, *args, root, **kwargs):
        super().__init__(server, *args, **kwargs)
        self.root = Path(root).resolve()
        self.requests = server.requests

    def local(self, path):
        target = (self.root / posixpath.normpath('/' + path).lstrip('/')).resolve()
        target.relative_to(self.root)
        return target

    def canonicalize(self, path):
        return posixpath.normpath('/' + path.lstrip('/'))

    def stat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(self.local(path).stat())
        except OSError as ex:
            return paramiko.SFTPServer.convert_errno(ex.errno)

    lstat = stat

    def list_folder(self, path):
        self.requests.append(('list', path))
        try:
            attrs = []
            for child in self.local(path).iterdir():
                a = paramiko.SFTPAttributes.from_stat(child.stat())
                a.filename = child.name
                attrs.append(a)
            return attrs
        except OSError as ex:
            return paramiko.SFTPServer.convert_errno(ex.errno)

    def open(self, path, flags, attr):
        self.requests.append(('open', path))
        try:
            fd = os.open(self.local(path), flags | getattr(os, 'O_BINARY', 0), 0o600)
            mode = 'r+b' if flags & os.O_RDWR else ('wb' if flags & os.O_WRONLY else 'rb')
            file = os.fdopen(fd, mode)
            handle = paramiko.SFTPHandle(flags)
            handle.readfile = file
            handle.writefile = file
            return handle
        except OSError as ex:
            return paramiko.SFTPServer.convert_errno(ex.errno)

    def remove(self, path):
        try:
            self.local(path).unlink()
            return paramiko.SFTP_OK
        except OSError as ex:
            return paramiko.SFTPServer.convert_errno(ex.errno)

    def mkdir(self, path, attr):
        try:
            self.local(path).mkdir()
            return paramiko.SFTP_OK
        except OSError as ex:
            return paramiko.SFTPServer.convert_errno(ex.errno)

    def rmdir(self, path):
        try:
            self.local(path).rmdir()
            return paramiko.SFTP_OK
        except OSError as ex:
            return paramiko.SFTPServer.convert_errno(ex.errno)

    def rename(self, old, new):
        try:
            if self.local(new).exists():
                return paramiko.SFTP_FAILURE
            self.local(old).rename(self.local(new))
            return paramiko.SFTP_OK
        except OSError as ex:
            return paramiko.SFTPServer.convert_errno(ex.errno)

    def chattr(self, path, attr):
        try:
            if attr.st_mtime is not None:
                os.utime(self.local(path), (attr.st_atime, attr.st_mtime))
            return paramiko.SFTP_OK
        except OSError as ex:
            return paramiko.SFTPServer.convert_errno(ex.errno)


class TransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = paramiko.RSAKey.generate(2048)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.env_patch = patch.dict(os.environ, LOCALAPPDATA=str(self.base))
        self.env_patch.start()
        self.public_key = None
        self.requests = []
        self.remote = self.base / 'phone'
        self.remote.mkdir()
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen()
        self.listener.settimeout(.1)
        self.stop = threading.Event()
        self.transports = []
        self.connections = []
        self.server_thread = threading.Thread(target=self.serve, daemon=True)
        self.server_thread.start()
        self.settings = Settings('127.0.0.1', self.listener.getsockname()[1], 'user', '/',
                                 str(self.base / 'downloads'), key_fingerprint(self.key))

    def serve(self):
        while not self.stop.is_set():
            try:
                client, address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            thread = threading.Thread(target=self.serve_connection, args=(client,), daemon=True)
            self.connections.append(thread)
            thread.start()

    def serve_connection(self, client):
        transport = paramiko.Transport(client)
        self.transports.append(transport)
        transport.add_server_key(self.key)
        transport.set_subsystem_handler('sftp', paramiko.SFTPServer, Files, root=self.remote)
        try:
            transport.start_server(server=Auth(self.public_key, self.requests))
            while transport.is_active() and not self.stop.wait(.05):
                pass
        except (EOFError, OSError, paramiko.SSHException):
            pass
        finally:
            transport.close()

    def tearDown(self):
        self.stop.set()
        self.listener.close()
        self.server_thread.join(2)
        for transport in self.transports:
            transport.close()
        for thread in self.connections:
            thread.join(2)
        self.env_patch.stop()
        self.temp.cleanup()

    def phone(self, cancel=None):
        return Phone(self.settings, 'test-password', cancel)

    def test_download_directory_default_and_legacy_migration(self):
        downloads = Path.home() / 'Downloads'
        self.assertEqual(Path(Settings().download_dir), downloads)
        legacy = replace(self.settings, download_dir=str(downloads / '手机互传'))
        legacy.save()
        migrated = Settings.load()
        self.assertEqual(Path(migrated.download_dir), downloads)
        self.assertEqual(migrated.fingerprint, self.settings.fingerprint)
        custom = replace(self.settings, download_dir=str(self.base / '自定义接收'))
        custom.save()
        self.assertEqual(Settings.load().download_dir, custom.download_dir)

    def test_roundtrip_folders_unicode_empty_files_duplicates_and_mtime(self):
        folder = self.base / '中文 相册'
        (folder / '子目录').mkdir(parents=True)
        payload = os.urandom(1024 * 1024 + 531)
        (folder / '大文件.bin').write_bytes(payload)
        (folder / '子目录' / '空文件.txt').write_bytes(b'')
        os.utime(folder / '大文件.bin', (1700000000, 1700000000))
        with self.phone() as phone:
            self.assertEqual(phone.upload([folder], '/'), 2)
            self.assertEqual(phone.upload([folder], '/'), 2)
            _, entries = phone.list_dir('/')
            self.assertEqual({e.name for e in entries}, {'中文 相册', '中文 相册 (1)'})
            dest = self.base / 'downloads'
            self.assertEqual(phone.download(entries, dest), 4)
            self.assertEqual(phone.download(entries[:1], dest), 2)
            self.assertEqual(len(list(dest.iterdir())), 3)
        copied = dest / '中文 相册' / '大文件.bin'
        self.assertEqual(hashlib.sha256(copied.read_bytes()).digest(), hashlib.sha256(payload).digest())
        self.assertEqual(int(copied.stat().st_mtime), 1700000000)
        self.assertEqual((dest / '中文 相册' / '子目录' / '空文件.txt').read_bytes(), b'')
        self.assertFalse(list(self.base.rglob('*.part')))

    def test_upload_and_download_file_duplicates_never_overwrite(self):
        source = self.base / 'same.txt'
        source.write_text('new', encoding='utf-8')
        (self.remote / 'same.txt').write_text('original', encoding='utf-8')
        dest = self.base / 'downloads'
        dest.mkdir()
        (dest / 'same.txt').write_text('local-original', encoding='utf-8')
        with self.phone() as phone:
            phone.upload([source], '/')
            _, files = phone.list_dir('/')
            phone.download([e for e in files if e.name == 'same.txt'], dest)
        self.assertEqual((self.remote / 'same.txt').read_text(), 'original')
        self.assertEqual((self.remote / 'same (1).txt').read_text(), 'new')
        self.assertEqual((dest / 'same.txt').read_text(), 'local-original')
        self.assertEqual((dest / 'same (1).txt').read_text(), 'original')

    def test_host_key_unknown_changed_and_bad_password(self):
        with self.assertRaises(UnknownHost) as context:
            with Phone(replace(self.settings, fingerprint=''), 'test-password'):
                pass
        self.assertEqual(context.exception.fingerprint, key_fingerprint(self.key))
        with self.assertRaises(paramiko.SSHException):
            with Phone(replace(self.settings, fingerprint='SHA256:wrong'), 'test-password'):
                pass
        with self.assertRaises(paramiko.AuthenticationException):
            with Phone(self.settings, 'bad-password'):
                pass

    def test_cancel_upload_and_download_clean_temporary_files(self):
        source = self.base / 'cancel.bin'
        source.write_bytes(os.urandom(2 * 1024 * 1024))
        cancel = threading.Event()

        def report(name, done, total, count):
            if done > 0:
                cancel.set()

        with self.phone(cancel) as phone:
            with self.assertRaises(Cancelled):
                phone.upload([source], '/', report)
        self.assertFalse(list(self.remote.iterdir()))
        (self.remote / source.name).write_bytes(source.read_bytes())
        cancel.clear()
        with self.phone(cancel) as phone:
            _, files = phone.list_dir('/')
            with self.assertRaises(Cancelled):
                phone.download(files, self.base / 'downloads', report)
        self.assertFalse(list((self.base / 'downloads').iterdir()))
        self.assertEqual((self.remote / source.name).stat().st_size, 2 * 1024 * 1024)

    def test_scan_classification_limit_and_root_boundary(self):
        (self.remote / 'DCIM').mkdir()
        (self.remote / 'DCIM' / 'a.JPG').write_bytes(b'photo')
        (self.remote / 'b.pdf').write_bytes(b'pdf')
        (self.remote / '.hidden').mkdir()
        (self.remote / '.hidden' / 'secret.txt').write_text('hidden')
        (self.remote / 'Android' / 'data').mkdir(parents=True)
        (self.remote / 'Android' / 'data' / 'private.txt').write_text('private')
        with self.phone() as phone:
            entries, skipped, truncated = phone.scan()
            self.assertEqual({e.name for e in entries}, {'a.JPG', 'b.pdf'})
            self.assertEqual({e.kind for e in entries}, {'image', 'document'})
            self.assertFalse(skipped)
            self.assertFalse(truncated)
            self.assertTrue(phone.scan(limit=1)[2])
            phone.root = '/DCIM'
            with self.assertRaises(ValueError):
                phone.list_dir('/Android')

    def test_thumbnail_and_names(self):
        from PIL import Image
        Image.new('RGB', (1200, 800), '#3b9c8d').save(self.remote / '图片.png')
        previews = self.remote / '.lc-transfer/previews'
        previews.mkdir(parents=True)
        Image.new('RGB', (320, 213), '#3b9c8d').save(previews / '图片.png.jpg', 'JPEG', quality=55)
        with self.phone() as phone:
            _, entries = phone.list_dir('/')
            thumb = phone.thumbnail(entries[0])
            with Image.open(thumb) as image:
                self.assertLessEqual(image.width, 240)
                self.assertLessEqual(image.height, 160)
            self.assertEqual(phone.thumbnail(entries[0]), thumb)
        self.assertNotIn(('open', '/图片.png'), self.requests)
        self.assertEqual(self.requests.count(('open', '/.lc-transfer/previews/图片.png.jpg')), 1)
        self.assertEqual(safe_name('../a:b.txt'), '.._a_b.txt')
        self.assertEqual(safe_name('CON.txt'), '_CON.txt')
        for name in ['../escape', '..', '/absolute', 'C:\\escape', '\x00']:
            with self.assertRaises(ValueError):
                validate_child(name)

    def test_zip_selection_folders_unicode_duplicates_and_cancellation(self):
        (self.remote / '相册').mkdir()
        (self.remote / '相册' / '照片.txt').write_text('你好 ZIP', encoding='utf-8')
        (self.remote / '空目录').mkdir()
        payload = os.urandom(1024 * 1024 + 701)
        (self.remote / 'big.bin').write_bytes(payload)
        events = []
        with self.phone() as phone:
            phone.events = events.append
            _, entries = phone.list_dir('/')
            count, path = phone.download_zip(entries + entries[:1], self.base / 'downloads')
            self.assertGreaterEqual(count, 2)
            with zipfile.ZipFile(path) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.read('相册/照片.txt').decode(), '你好 ZIP')
                self.assertEqual(archive.read('big.bin'), payload)
                self.assertIn('空目录/', archive.namelist())
                self.assertEqual(len(archive.namelist()), len(set(n.casefold() for n in archive.namelist())))
            self.assertTrue(any(e['state'] == 'packed' for e in events))
        cancel = threading.Event()
        def report(name, done, total, count):
            if done:
                cancel.set()
        with self.phone(cancel) as phone:
            _, entries = phone.list_dir('/')
            with self.assertRaises(Cancelled):
                phone.download_zip(entries, self.base / 'cancelled-zip', report)
        self.assertFalse(list((self.base / 'cancelled-zip').iterdir()))

    def test_upload_can_sync_history_between_pipelined_write_batches(self):
        from transfer_history import History
        source = self.base / 'large-file-with-ampersand&sample.zip'
        payload = os.urandom(20 * 1024 * 1024 + 137)
        source.write_bytes(payload)
        from discovery import identity
        self.public_key = identity()
        self.settings.key_auth = True
        history = History()
        job = history.start('upload', [source.name], '/', self.settings.host, self.settings.fingerprint)
        synced_at = []
        with self.phone() as phone:
            phone.sftp.get_channel().settimeout(3)
            def event(data):
                history.event(job, data)
                if data['state'] == 'running' and data['done'] and (
                        not synced_at or data['done'] - synced_at[-1] >= 1024 * 1024):
                    history.mirror(phone)
                    self.assertFalse(history.warning)
                    synced_at.append(data['done'])
            phone.events = event
            self.assertEqual(phone.upload([source], '/'), 1)
            history.finish(job, 'done')
            history.mirror(phone)
        self.assertGreaterEqual(len(synced_at), 3)
        self.assertEqual((self.remote / source.name).read_bytes(), payload)
        self.assertEqual(source.read_bytes(), payload)
        self.assertFalse(list(self.remote.glob('.hut-chuan-*.part')))
        self.assertEqual(history.snapshot()['jobs'][0]['state'], 'done')

    def test_upload_timeout_describes_file_and_cleans_partial(self):
        source = self.base / 'timeout.bin'
        payload = os.urandom(1024 * 1024)
        source.write_bytes(payload)
        with self.phone() as phone:
            def fail_after_progress(data):
                if data['done']:
                    raise TimeoutError()
            phone.events = fail_after_progress
            with self.assertRaisesRegex(TimeoutError, 'timeout.bin.*超时.*已确认'):
                phone.upload([source], '/')
        self.assertEqual(source.read_bytes(), payload)
        self.assertFalse((self.remote / source.name).exists())
        self.assertFalse(list(self.remote.glob('.hut-chuan-*.part')))

    def test_history_mirrors_progress_and_completed_files_to_phone(self):
        from transfer_history import History
        history = History()
        job = history.start('upload', ['test.txt'], '/', self.settings.host)
        event = {'path': 'test.txt', 'name': 'test.txt', 'done': 12, 'size': 12,
                 'state': 'done', 'target': '/test.txt'}
        history.event(job, event)
        history.finish(job, 'done')
        with self.phone() as phone:
            history.mirror(phone)
        import json
        data = json.loads((self.remote / '.lc-transfer' / 'history.json').read_text(encoding='utf-8'))
        self.assertEqual(data['jobs'][0]['files'][0]['name'], 'test.txt')
        self.assertEqual(data['jobs'][0]['state'], 'done')
        self.assertEqual(data['jobs'][0]['files'][0]['target'], '/test.txt')
        self.assertFalse(history.warning)
        zip_job = history.start('download', ['test.txt'], '/', self.settings.host)
        history.event(zip_job, dict(event, state='packed'))
        history.finish(zip_job, 'cancelled')
        self.assertEqual(history.snapshot()['jobs'][0]['files'][0]['state'], 'discarded')

    def test_history_follows_paired_phone_across_network_addresses(self):
        import json
        from transfer_history import History
        history = History()
        job = history.start('download', ['photo.jpg'], 'Downloads', '198.51.100.2')
        history.finish(job, 'done')
        history.bind_device('198.51.100.2', self.settings.fingerprint)
        other = history.start('download', ['other.jpg'], 'Downloads', self.settings.host, 'SHA256:other-phone')
        history.finish(other, 'done')
        with self.phone() as phone:
            history.mirror(phone)
        mirrored = json.loads((self.remote / '.lc-transfer/history.json').read_text())
        self.assertEqual([item['id'] for item in mirrored['jobs']], [job])


if __name__ == '__main__':
    unittest.main()
