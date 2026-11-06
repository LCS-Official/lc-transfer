"""SFTP operations shared by the desktop UI and integration checks."""
from __future__ import annotations

import base64
import errno
import hashlib
import io
import gzip
import json
import logging
import os
import posixpath
import re
import select
import socket
import stat
import threading
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import paramiko
from PIL import Image

IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.heic', '.heif', '.avif'}
VIDEO_EXT = {'.mp4', '.mkv', '.mov', '.avi', '.webm', '.3gp', '.m4v', '.ts'}
AUDIO_EXT = {'.mp3', '.flac', '.wav', '.aac', '.m4a', '.ogg', '.opus', '.wma'}
DOC_EXT = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.md', '.csv', '.epub', '.zip', '.7z', '.rar', '.tar', '.gz', '.apk'}


def data_dir() -> Path:
    path = Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'LCTransfer'
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Settings:
    host: str = ''
    port: int = 52022
    username: str = 'user'
    root: str = '/storage/emulated/0'
    download_dir: str = str(Path.home() / 'Downloads')
    fingerprint: str = ''
    key_auth: bool = False
    receive_dir: str = '/storage/emulated/0/Download/互传'
    fallback_host: str = ''
    fallback_port: int = 52022

    @classmethod
    def load(cls):
        try:
            d = json.loads((data_dir() / 'settings.json').read_text(encoding='utf-8'))
            if Path(d.get('download_dir', '')) == Path.home() / 'Downloads' / '手机互传':
                d['download_dir'] = str(Path.home() / 'Downloads')
            return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self):
        target = data_dir() / 'settings.json'
        temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, target)


@dataclass(frozen=True)
class Entry:
    path: str
    name: str
    size: int
    mtime: float
    is_dir: bool = False

    @property
    def kind(self) -> str:
        ext = posixpath.splitext(self.name)[1].lower()
        if self.is_dir:
            return 'folder'
        for name, extensions in [('image', IMAGE_EXT), ('video', VIDEO_EXT), ('audio', AUDIO_EXT), ('document', DOC_EXT)]:
            if ext in extensions:
                return name
        return 'other'


class Cancelled(Exception):
    pass


class CancelEvent(threading.Event):
    """Cancellation wakes pending network I/O as well as cooperative checks."""
    def __init__(self):
        super().__init__()
        self._abort_lock = threading.Lock()
        self._aborters = set()

    def register(self, abort):
        with self._abort_lock:
            cancelled = self.is_set()
            if not cancelled:
                self._aborters.add(abort)
        if cancelled:
            abort()

    def unregister(self, abort):
        with self._abort_lock:
            self._aborters.discard(abort)

    def set(self):
        super().set()
        with self._abort_lock:
            aborters = tuple(self._aborters)
            self._aborters.clear()
        for abort in aborters:
            abort()


class UnknownHost(Exception):
    def __init__(self, fingerprint: str):
        self.fingerprint = fingerprint
        super().__init__(f'首次连接，需要确认手机的 SSH 指纹：\n{fingerprint}')


def key_fingerprint(key):
    return 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


class PinnedHostKey(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected):
        self.expected = expected

    def missing_host_key(self, client, hostname, key):
        actual = key_fingerprint(key)
        if not self.expected:
            raise UnknownHost(actual)
        if actual != self.expected:
            raise paramiko.SSHException('手机 SSH 指纹发生变化，已停止连接。请核实设备；重新安装手机端后可在连接设置清除旧指纹。')


def human_size(size: int) -> str:
    value = float(size)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if value < 1024 or unit == 'TB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024


def safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).rstrip(' .')
    if not name or name in {'.', '..'}:
        name = '_'
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', name, re.I):
        name = '_' + name
    # Keep a valid extension and room for the duplicate suffix on Windows.
    if len(name) > 160:
        stem, ext = os.path.splitext(name)
        name = stem[:140] + ext[:20]
    return name


def numbered_name(name, index):
    if index == 0:
        return name
    stem, ext = os.path.splitext(name)
    return f'{stem} ({index}){ext}'


def validate_child(name):
    if not name or name in {'.', '..'} or '/' in name or '\\' in name or '\x00' in name:
        raise ValueError('手机返回了无效文件名，已停止访问。')


class Phone:
    def __init__(self, settings: Settings, password: str, cancel=None, events=None):
        self.settings = settings
        self.password = password
        self.cancel = cancel or threading.Event()
        self.ssh = None
        self._socket = None
        self.sftp = None
        self.root = ''
        self.events = events or (lambda event: None)

    def event(self, entry, done, total, state, target=''):
        self.events({'path': entry.path, 'name': entry.name, 'done': done,
                     'size': total, 'state': state, 'target': str(target)})

    def check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled('操作已取消，已完成的文件会保留。')

    def read_blocks(self, source, size):
        prefetch = source._prefetch_thread
        def cancelled_prefetch(*args):
            try:
                prefetch(*args)
            except (EOFError, OSError, paramiko.SSHException):
                if not self.cancel.is_set():
                    raise
        # Paramiko has no public prefetch cancellation hook; suppress only our forced-close errors.
        source._prefetch_thread = cancelled_prefetch
        try:
            for start in range(0, size, 4 * 1024 * 1024):
                self.check_cancel()
                chunks = [(offset, min(32768, size - offset))
                          for offset in range(start, min(size, start + 4 * 1024 * 1024), 32768)]
                # Fit this bounded batch in the request limit so no limiter waits after socket abort.
                blocks = list(source.readv(chunks, max_concurrent_prefetch_requests=len(chunks)))
                for block in blocks:
                    self.check_cancel()
                    yield block
        finally:
            source._prefetch_thread = prefetch

    def abort(self):
        sock = self._socket
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def _connect_socket(self):
        host, port = self.settings.host.strip(), int(self.settings.port)
        errors = {}
        for family, kind, proto, _, address in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM):
            self.check_cancel()
            sock = self._socket = socket.socket(family, kind, proto)
            try:
                sock.setblocking(False)
                pending = {0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY,
                           10035, 10036, 10037}
                result = sock.connect_ex(address)
                if result not in pending:
                    raise OSError(result, os.strerror(result))
                deadline = time.monotonic() + 10
                while result:
                    self.check_cancel()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('连接手机超时。')
                    _, writable, failed = select.select([], [sock], [sock], min(.05, remaining))
                    if writable or failed:
                        result = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                        if result:
                            raise OSError(result, os.strerror(result))
                self.check_cancel()
                sock.settimeout(10)
                return sock
            except OSError as error:
                sock.close()
                self.check_cancel()
                errors[address] = error
        raise paramiko.ssh_exception.NoValidConnectionsError(errors)

    def __enter__(self):
        self.check_cancel()
        if not self.settings.host.strip() or not 1 <= int(self.settings.port) <= 65535:
            raise ValueError('请填写有效的手机地址和端口。')
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(PinnedHostKey(self.settings.fingerprint))
        if isinstance(self.cancel, CancelEvent):
            self.cancel.register(self.abort)
        try:
            pkey = None
            if self.settings.key_auth:
                from discovery import identity
                pkey = identity()
            self.ssh.connect(self.settings.host.strip(), port=int(self.settings.port),
                             username=self.settings.username, password=self.password or None, pkey=pkey,
                             allow_agent=False, look_for_keys=False, timeout=10,
                             auth_timeout=15, banner_timeout=15, channel_timeout=15,
                             sock=self._connect_socket())
            transport = self.ssh.get_transport()
            transport.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
            transport.set_keepalive(20)
            self.sftp = self.ssh.open_sftp()
            self.sftp.get_channel().settimeout(25)
            self.root = self.sftp.normalize(self.settings.root or '/')
            if not stat.S_ISDIR(self.sftp.stat(self.root).st_mode):
                raise ValueError('手机共享根目录不是文件夹。')
            return self
        except BaseException:
            self.__exit__()
            self.check_cancel()
            raise

    def device_config(self):
        """Read the receiving directory only after authenticating the phone."""
        try:
            with self.sftp.open(posixpath.join(self.root, '.lc-transfer/device.json'), 'rb') as source:
                data = json.loads(source.read(8193))
            path = self.checked(data['receive_dir'])
            if not stat.S_ISDIR(self.sftp.stat(path).st_mode):
                raise ValueError('手机接收目录不是文件夹。')
            return path
        except FileNotFoundError:
            if self.settings.key_auth:
                raise ValueError('手机配置尚未就绪，请重新打开手机端。')
            return self.settings.receive_dir

    def pending_share(self):
        """Read one phone-initiated request; its filenames never choose the PC directory."""
        if not self.settings.key_auth:
            return None
        base = posixpath.join(self.root, '.lc-transfer/outbox')
        try:
            folders = self.sftp.listdir_attr(base)
        except FileNotFoundError:
            return None
        for attr in sorted(folders, key=lambda item: item.st_mtime or 0)[:1000]:
            if not stat.S_ISDIR(attr.st_mode) or not re.fullmatch(r'[0-9a-f]{32}', attr.filename):
                continue
            folder = posixpath.join(base, attr.filename)
            if self.checked(folder) != folder:
                raise ValueError('手机分享目录路径已变化。')
            try:
                with self.sftp.open(posixpath.join(folder, 'request.json'), 'rb') as stream:
                    payload = stream.read(512 * 1024 + 1)
            except FileNotFoundError:
                continue
            if len(payload) > 512 * 1024:
                raise ValueError('手机分享请求过大。')
            data = json.loads(payload)
            if (not isinstance(data, dict) or data.get('version') != 1 or data.get('id') != attr.filename
                    or not isinstance(data.get('files'), list) or not 1 <= len(data['files']) <= 1000):
                raise ValueError('手机分享请求格式无效。')
            entries = []
            for index, row in enumerate(data['files']):
                if (not isinstance(row, dict) or row.get('file') != str(index)
                        or not isinstance(row.get('name'), str)
                        or not isinstance(row.get('size'), int) or isinstance(row['size'], bool) or row['size'] < 0
                        or not isinstance(row.get('mtime'), int) or isinstance(row['mtime'], bool)
                        or not 0 <= row['mtime'] < 10**12):
                    raise ValueError('手机分享文件信息无效。')
                validate_child(row['name'])
                entries.append(Entry(posixpath.join(folder, str(index)), row['name'], row['size'], row['mtime']))
            request = {'id': data['id'], 'entries': entries}
            try:
                self.sftp.stat(posixpath.join(folder, 'received'))
            except FileNotFoundError:
                for entry in entries:
                    if self.checked(entry.path) != entry.path:
                        raise ValueError('手机分享文件路径已变化。')
                    current = self.sftp.lstat(entry.path)
                    if (not stat.S_ISREG(current.st_mode) or current.st_size != entry.size
                            or int(current.st_mtime) != entry.mtime):
                        raise ValueError('手机分享文件已变化或尚未准备完成。')
                return request
            self.complete_share(request)
        return None

    def complete_share(self, request):
        """Acknowledge before removing only the temporary copies created for this request."""
        if not re.fullmatch(r'[0-9a-f]{32}', request['id']):
            raise ValueError('分享任务 ID 无效。')
        folder = posixpath.join(self.root, '.lc-transfer/outbox', request['id'])
        if self.checked(folder) != folder:
            raise ValueError('手机分享目录路径已变化。')
        paths = [posixpath.join(folder, str(i)) for i in range(len(request['entries']))]
        if [entry.path for entry in request['entries']] != paths:
            raise ValueError('分享文件路径无效。')
        marker = posixpath.join(folder, 'received')
        with self.sftp.open(marker, 'wb') as stream:
            stream.write(b'1')
        for path in paths + [posixpath.join(folder, 'request.json')]:
            if self.checked(path) != path:
                raise ValueError('分享临时文件路径已变化。')
            try:
                self.sftp.remove(path)
            except FileNotFoundError:
                pass
        self.sftp.remove(marker)
        self.sftp.rmdir(folder)

    def heartbeat(self):
        folder = self.checked(posixpath.join(self.root, '.lc-transfer'))
        with self.sftp.open(posixpath.join(folder, 'session.json'), 'wb') as output:
            output.write(b'{"online":true}')

    def __exit__(self, exc_type=None, exc=None, traceback=None):
        try:
            try:
                if self.sftp:
                    self.sftp.close()
            finally:
                if self.ssh:
                    self.ssh.close()
                if self._socket is not None:
                    self._socket.close()
                if isinstance(self.cancel, CancelEvent):
                    self.cancel.unregister(self.abort)
        except Exception:
            self.check_cancel()
            raise
        if exc_type is not None:
            self.check_cancel()

    def checked(self, path):
        self.check_cancel()
        actual = self.sftp.normalize(path)
        root = self.root.rstrip('/')
        if actual != self.root and root and not actual.startswith(root + '/'):
            raise ValueError('路径超出了手机共享根目录。')
        return actual

    def list_dir(self, path):
        path = self.checked(path)
        entries = []
        for a in self.sftp.listdir_attr(path):
            self.check_cancel()
            validate_child(a.filename)
            if stat.S_ISLNK(a.st_mode) or a.filename == '.lc-transfer' or a.filename.startswith('.hut-chuan-'):
                continue
            entries.append(Entry(posixpath.join(path, a.filename), a.filename,
                                 a.st_size or 0, a.st_mtime or 0, stat.S_ISDIR(a.st_mode)))
        return path, entries

    def delete_files(self, entries, report=lambda *args: None):
        targets = {}
        for entry in entries:
            if entry.is_dir:
                raise ValueError('请仅选择文件；不删除文件夹。')
            path = self.checked(entry.path)
            relative = posixpath.relpath(path, self.root)
            if (path != posixpath.normpath(entry.path) or not entry.path.startswith('/')
                    or relative == '.' or relative.split('/')[0] == '.lc-transfer'):
                raise ValueError('不能删除共享根目录、应用状态文件或已变化的路径。')
            targets[path] = entry
        removed = []
        for path, entry in targets.items():
            self.check_cancel()
            attr = self.sftp.lstat(path)
            if not stat.S_ISREG(attr.st_mode):
                raise ValueError(f'{entry.name} 不是普通文件，请刷新后重试。')
            if attr.st_size != entry.size or int(attr.st_mtime) != int(entry.mtime):
                raise ValueError(f'{entry.name} 在列表加载后发生变化，请刷新后重新选择。')
            self.sftp.remove(path)
            removed.append(path)
            logging.info('deleted phone file host=%s path=%s', self.settings.host, path)
            report(path, len(removed), len(targets))
        return removed

    def scan(self, report=lambda s: None, limit=100000):
        report('正在读取手机媒体索引…')
        try:
            with self.sftp.open(posixpath.join(self.root, '.lc-transfer/catalog.json.gz'), 'rb') as source:
                packed = source.read(12 * 1024 * 1024 + 1)
            if len(packed) > 12 * 1024 * 1024:
                raise ValueError('手机媒体索引超过大小限制。')
            with gzip.GzipFile(fileobj=io.BytesIO(packed)) as source:
                raw = source.read(64 * 1024 * 1024 + 1)
            if len(raw) > 64 * 1024 * 1024:
                raise ValueError('手机媒体索引解压后超过大小限制。')
            data = json.loads(raw)
            if data.get('version') != 1 or not isinstance(data.get('files'), list) or len(data['files']) > 100000:
                raise ValueError('手机媒体索引格式无效。')
            entries = []
            for row in data['files'][:limit]:
                self.check_cancel()
                if not isinstance(row, list) or len(row) != 3 or not isinstance(row[0], str):
                    raise ValueError('手机媒体索引包含无效记录。')
                relative, size, mtime = row
                path = posixpath.normpath(posixpath.join(self.root, relative))
                root = self.root.rstrip('/')
                if (relative.startswith('/') or '\x00' in relative or relative.startswith('../')
                        or (root and not path.startswith(root + '/')) or path == self.root
                        or not isinstance(size, int) or isinstance(size, bool) or size < 0
                        or not isinstance(mtime, (int, float)) or isinstance(mtime, bool) or not 0 <= mtime < 1e12):
                    raise ValueError('手机媒体索引包含无效路径或文件属性。')
                validate_child(posixpath.basename(path))
                entries.append(Entry(path, posixpath.basename(path), size, mtime))
            return entries, [], bool(data.get('truncated')) or len(data['files']) > limit
        except FileNotFoundError:
            if self.settings.key_auth:
                raise ValueError('请更新手机端 APK，以启用快速媒体索引和小尺寸缩略图。')
        # Legacy services lack MediaStore support; directory browsing still works with them.
        pending = [self.root]
        entries, skipped = [], []
        while pending:
            self.check_cancel()
            folder = pending.pop()
            try:
                _, children = self.list_dir(folder)
            except OSError as ex:
                if folder == self.root:
                    raise
                skipped.append(f'{folder}: {ex}')
                continue
            for e in children:
                if e.is_dir:
                    if not e.name.startswith('.') and e.path.rstrip('/').lower() not in {
                        posixpath.join(self.root, 'Android/data').lower(),
                        posixpath.join(self.root, 'Android/obb').lower(),
                    }:
                        pending.append(e.path)
                else:
                    entries.append(e)
                    if len(entries) >= limit:
                        return entries, skipped, True
            report(f'正在索引手机文件：{len(entries):,} 个文件 · {folder}')
        return entries, skipped, False

    def thumbnail(self, entry):
        self.check_cancel()
        if entry.kind not in {'image', 'video'}:
            return None
        if entry.path != posixpath.normpath(entry.path):
            raise ValueError('缩略图源路径无效。')
        cache = data_dir() / 'thumbnails'
        cache.mkdir(exist_ok=True)
        device = self.settings.fingerprint or f'{self.settings.host}:{self.settings.port}'
        token = '|'.join(['phone-preview-v1', device,
                          entry.path, str(entry.mtime), str(entry.size)])
        dest = cache / (hashlib.sha256(token.encode()).hexdigest() + '.jpg')
        if dest.exists():
            return str(dest)
        root = self.root.rstrip('/')
        if root and not entry.path.startswith(root + '/'):
            raise ValueError('缩略图源路径超出了共享存储。')
        relative = posixpath.relpath(entry.path, self.root)
        if relative.startswith('../') or relative.startswith('/') or '\x00' in relative:
            raise ValueError('缩略图源路径无效。')
        preview = posixpath.join(self.root, '.lc-transfer/previews', relative + '.jpg')
        try:
            with self.sftp.open(preview, 'rb') as remote:
                payload = remote.read(96 * 1024 + 1)
        except FileNotFoundError:
            return None
        self.check_cancel()
        if len(payload) > 96 * 1024:
            return None
        with Image.open(io.BytesIO(payload)) as im:
            if im.width > 320 or im.height > 320:
                return None
            im.thumbnail((240, 160))
            im.convert('RGB').save(dest, 'JPEG', quality=65)
        return str(dest)

    def _remote_unused(self, folder, name):
        for index in range(10000):
            candidate = posixpath.join(folder, numbered_name(name, index))
            try:
                self.sftp.lstat(candidate)
            except OSError as ex:
                if ex.errno == errno.ENOENT:
                    return candidate
                raise
        raise ValueError('重名文件过多，请换一个目录。')

    def download(self, entries, dest: Path, report=lambda *a: None):
        dest = dest.resolve()
        dest.mkdir(parents=True, exist_ok=True)
        count = 0

        def copy(entry, folder):
            nonlocal count
            self.check_cancel()
            remote = self.checked(entry.path)
            attr = self.sftp.lstat(remote)
            if stat.S_ISLNK(attr.st_mode):
                raise ValueError('不传输符号链接。')
            name = safe_name(entry.name)
            if stat.S_ISDIR(attr.st_mode):
                for i in range(10000):
                    target = folder / numbered_name(name, i)
                    try:
                        target.mkdir()
                        break
                    except FileExistsError:
                        continue
                else:
                    raise ValueError('重名文件夹过多。')
                _, children = self.list_dir(remote)
                for child in children:
                    copy(child, target)
                os.utime(target, (entry.mtime, entry.mtime))
                return
            temp = folder / ('.hut-chuan-' + uuid.uuid4().hex + '.part')
            done = 0
            try:
                with self.sftp.open(remote, 'rb') as source, temp.open('xb') as output:
                    self.event(entry, 0, attr.st_size or 0, 'running', folder)
                    report(entry.name, 0, attr.st_size or 0, count)
                    for block in self.read_blocks(source, attr.st_size):
                        output.write(block)
                        done += len(block)
                        self.event(entry, done, attr.st_size or 0, 'running', folder)
                        report(entry.name, done, attr.st_size or 0, count)
                    output.flush()
                    os.fsync(output.fileno())
                latest = self.sftp.stat(remote)
                if done != attr.st_size or latest.st_size != attr.st_size or latest.st_mtime != attr.st_mtime:
                    raise IOError(f'{entry.name} 在下载过程中发生变化，请重试。')
                os.utime(temp, (entry.mtime, entry.mtime))
                for i in range(10000):
                    target = folder / numbered_name(name, i)
                    try:
                        # Windows rename refuses to overwrite; hardlink does so on POSIX too.
                        if os.name == 'nt':
                            os.rename(temp, target)
                        else:
                            os.link(temp, target)
                            temp.unlink()
                        break
                    except FileExistsError:
                        continue
                else:
                    raise ValueError('重名文件过多。')
                count += 1
                self.event(entry, done, done, 'done', target)
            finally:
                temp.unlink(missing_ok=True)

        for entry in entries:
            copy(entry, dest)
        return count

    def download_zip(self, entries, dest: Path, report=lambda *a: None):
        """Stream selected phone files directly into one local ZIP64 archive."""
        dest = dest.resolve()
        dest.mkdir(parents=True, exist_ok=True)
        temp = dest / ('.hut-chuan-' + uuid.uuid4().hex + '.part')
        archive_name = time.strftime('手机文件_%Y%m%d_%H%M%S') + '.zip'
        used = set()
        count = 0

        def member_name(parent, name):
            name = safe_name(name)
            for i in range(10000):
                result = parent + numbered_name(name, i)
                if result.casefold() not in used:
                    used.add(result.casefold())
                    return result
            raise ValueError('ZIP 中的重名文件过多。')

        def add(archive, entry, parent=''):
            nonlocal count
            self.check_cancel()
            remote = self.checked(entry.path)
            attr = self.sftp.lstat(remote)
            if stat.S_ISLNK(attr.st_mode):
                raise ValueError('不传输符号链接。')
            member = member_name(parent, entry.name)
            date = list(time.localtime(entry.mtime)[:6])
            date[0] = max(1980, min(2107, date[0]))
            info = zipfile.ZipInfo(member + ('/' if entry.is_dir else ''), tuple(date))
            info.compress_type = zipfile.ZIP_STORED
            if stat.S_ISDIR(attr.st_mode):
                archive.writestr(info, b'')
                _, children = self.list_dir(remote)
                for child in children:
                    add(archive, child, member + '/')
                return
            done = 0
            info.file_size = attr.st_size
            self.event(entry, 0, attr.st_size, 'running', archive_name + '/' + member)
            report(entry.name, 0, attr.st_size, count)
            with self.sftp.open(remote, 'rb') as source, archive.open(info, 'w', force_zip64=True) as output:
                for block in self.read_blocks(source, attr.st_size):
                    output.write(block)
                    done += len(block)
                    self.event(entry, done, attr.st_size, 'running', archive_name + '/' + member)
                    report(entry.name, done, attr.st_size, count)
            latest = self.sftp.stat(remote)
            if done != attr.st_size or latest.st_size != attr.st_size or latest.st_mtime != attr.st_mtime:
                raise IOError(f'{entry.name} 在传输过程中发生变化，请重试。')
            count += 1
            self.event(entry, done, done, 'packed', archive_name + '/' + member)

        try:
            with temp.open('xb') as output:
                with zipfile.ZipFile(output, 'w', allowZip64=True) as archive:
                    for entry in entries:
                        add(archive, entry)
                output.flush()
                os.fsync(output.fileno())
            self.check_cancel()
            for i in range(10000):
                target = dest / numbered_name(archive_name, i)
                try:
                    if os.name == 'nt':
                        os.rename(temp, target)
                    else:
                        os.link(temp, target)
                        temp.unlink()
                    return count, str(target)
                except FileExistsError:
                    continue
            raise ValueError('接收目录中的重名 ZIP 过多。')
        finally:
            temp.unlink(missing_ok=True)

    def upload(self, paths, dest, report=lambda *a: None):
        dest = self.checked(dest)
        count = 0
        packet_size = 128 * 1024 if self.settings.key_auth else 32768

        def copy(source: Path, folder):
            nonlocal count
            self.check_cancel()
            if source.is_symlink() or (hasattr(source, 'is_junction') and source.is_junction()):
                raise ValueError('不传输符号链接或目录联接，请选择实际文件。')
            validate_child(source.name)
            target = self._remote_unused(folder, source.name)
            attr = source.stat()
            entry = Entry(str(source), source.name, attr.st_size, attr.st_mtime)
            if source.is_dir():
                self.sftp.mkdir(target)
                for child in source.iterdir():
                    copy(child, target)
                self.sftp.utime(target, (attr.st_atime, attr.st_mtime))
                return
            if not source.is_file():
                raise ValueError(f'{source} 不是普通文件。')
            temp = posixpath.join(folder, '.hut-chuan-' + uuid.uuid4().hex + '.part')
            done = 0
            try:
                with source.open('rb') as incoming, self.sftp.open(temp, 'wx') as output:
                    output.MAX_REQUEST_SIZE = packet_size
                    output.set_pipelined(True)
                    self.event(entry, 0, attr.st_size, 'running', target)
                    report(source.name, 0, attr.st_size, count)
                    while True:
                        self.check_cancel()
                        block = incoming.read(8 * 1024 * 1024)
                        if not block:
                            break
                        # Drain this batch's ACKs before callbacks make other SFTP requests.
                        output.write(block[:-packet_size])
                        output.set_pipelined(False)
                        output.write(block[-packet_size:])
                        output.set_pipelined(True)
                        done += len(block)
                        self.event(entry, done, attr.st_size, 'running', target)
                        report(source.name, done, attr.st_size, count)
                latest = source.stat()
                if done != attr.st_size or latest.st_mtime_ns != attr.st_mtime_ns or latest.st_size != attr.st_size:
                    raise IOError(f'{source.name} 在上传过程中发生变化，请重试。')
                if self.sftp.stat(temp).st_size != attr.st_size:
                    raise IOError(f'{source.name} 上传长度校验失败。')
                self.sftp.utime(temp, (attr.st_atime, attr.st_mtime))
                # Standard SFTP rename fails if the destination already exists.
                self.sftp.rename(temp, target)
                count += 1
                self.event(entry, done, done, 'done', target)
            except TimeoutError as ex:
                raise TimeoutError(f'上传 {source.name} 等待手机响应超时（已确认 {human_size(done)} / {human_size(attr.st_size)}）。') from ex
            finally:
                try:
                    self.sftp.remove(temp)
                except OSError:
                    pass

        for path in paths:
            copy(Path(path), dest)
        return count
