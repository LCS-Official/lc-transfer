import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop import Entry, TransferDialog, Window


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')

    def test_multiselect_date_sort_filter_paging_and_transfer_window(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, LOCALAPPDATA=temp):
            window = Window(auto_discover=False)
            window.start_thumbnails = lambda *args: None
            now = time.time() - 60
            window.connected = True
            window.category = 'files'
            window.entries = [Entry(f'/DCIM/照片{i:03d}.jpg', f'照片{i:03d}.jpg', 52022, now - i * 3600)
                              for i in range(125)]
            window.show()
            window.render()
            self.app.processEvents()
            self.assertEqual(len(window.filtered()), 125)
            self.assertEqual(window.filtered()[0].name, '照片000.jpg')
            gallery = window.galleries[0]
            self.assertGreaterEqual(gallery.count(), 2)
            first = gallery.visualItemRect(gallery.item(0)).center()
            second = gallery.visualItemRect(gallery.item(1)).center()
            QTest.mouseClick(gallery.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, first)
            QTest.mouseClick(gallery.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, second)
            self.assertEqual(len(window.selected()), 2)
            self.assertTrue(window.download_button.isEnabled())
            window.change_page(1)
            self.assertEqual(window.page, 1)
            self.assertFalse(window.selected())
            window.sort.setCurrentIndex(1)
            self.assertEqual(window.filtered()[0].name, '照片124.jpg')
            window.search.setText('照片001')
            self.assertEqual(len(window.filtered()), 1)
            window.search.clear()
            job = window.history.start('download', ['照片000.jpg'], temp, '198.51.100.2')
            window.history.event(job, {'path':'/DCIM/照片000.jpg','name':'照片000.jpg', 'done':100,
                                      'size':52022,'state':'running','target':'test.zip/照片000.jpg'})
            window.show_transfers()
            window.transfer_dialog.refresh()
            self.assertEqual(window.transfer_dialog.tree.topLevelItemCount(), 1)
            self.assertEqual(window.transfer_dialog.tree.topLevelItem(0).childCount(), 1)
            window.history.finish(job, 'done', archive=str(Path(temp) / 'test.zip'))
            window.transfer_dialog.refresh()
            self.assertEqual(window.transfer_dialog.tree.topLevelItem(0).text(2), '已完成')
            window.transfer_dialog.close()
            window.connected = False
            window.close()
            self.app.processEvents()

    def test_actual_window_downloads_single_files_and_zip_with_live_history(self):
        from unittest.mock import patch
        from test_transfer import TransferTests
        import zipfile
        fixture = TransferTests('test_thumbnail_and_names')
        TransferTests.setUpClass()
        fixture.setUp()
        errors = []
        try:
            payloads = {'照片.jpg': b'original jpg bytes', '视频.mp4': b'original video bytes'}
            for name, content in payloads.items():
                (fixture.remote / name).write_bytes(content)
            folder = fixture.remote / '单独文件夹'
            folder.mkdir()
            (folder / '说明.txt').write_text('folder contents', encoding='utf-8')
            receiver = Path(fixture.settings.download_dir)
            receiver.mkdir()
            (receiver / '照片.jpg').write_bytes(b'existing local photo')
            with patch.dict(os.environ, LOCALAPPDATA=str(fixture.base)), patch('desktop.QMessageBox.warning', side_effect=lambda *a: errors.append(a)):
                window = Window(auto_discover=False)
                window.start_thumbnails = lambda *args: None
                window.settings = fixture.settings
                window.password = 'test-password'
                window.show()
                window.connect_phone()

                def wait_finished():
                    deadline = time.monotonic() + 30
                    while window.busy and time.monotonic() < deadline:
                        self.app.processEvents()
                        QTest.qWait(10)
                    self.assertFalse(window.busy)
                    self.assertFalse(errors)

                wait_finished()
                self.assertTrue(window.connected)
                self.assertEqual(window.category, 'recent')
                self.assertTrue(window.nav['recent'].isChecked())
                window.choose_category('files')
                wait_finished()
                files = [e for e in window.entries if not e.is_dir]
                for entry in files:
                    window.transfer('download', [entry])
                    wait_finished()
                    job = window.history.snapshot()['jobs'][0]
                    item = job['files'][0]
                    self.assertEqual(job['state'], 'done')
                    self.assertEqual(job['archive'], '')
                    self.assertEqual(item['state'], 'done')
                    saved = Path(item['target'])
                    self.assertEqual(saved.parent, receiver)
                    self.assertEqual(saved.read_bytes(), payloads[entry.name])
                    self.assertIn(str(saved), window.status.text())
                self.assertEqual((receiver / '照片.jpg').read_bytes(), b'existing local photo')
                self.assertEqual((receiver / '照片 (1).jpg').read_bytes(), payloads['照片.jpg'])
                self.assertFalse(list(receiver.glob('*.zip')))
                window.transfer('download', files)
                wait_finished()
                job = window.history.snapshot()['jobs'][0]
                self.assertEqual(job['state'], 'done')
                self.assertTrue(job['archive'].endswith('.zip'))
                self.assertEqual(job['files'][0]['state'], 'done')
                with zipfile.ZipFile(job['archive']) as archive:
                    for name, content in payloads.items():
                        self.assertEqual(archive.read(name), content)
                window.transfer('download', [e for e in window.entries if e.is_dir])
                wait_finished()
                job = window.history.snapshot()['jobs'][0]
                self.assertTrue(job['archive'].endswith('.zip'))
                with zipfile.ZipFile(job['archive']) as archive:
                    self.assertEqual(archive.read('单独文件夹/说明.txt').decode(), 'folder contents')
                self.assertTrue((fixture.remote / '.lc-transfer' / 'history.json').exists())
                window.transfer_dialog.close()
                window.close()
                self.app.processEvents()
        finally:
            fixture.tearDown()

    def test_media_scroll_preloads_one_batch_and_keeps_selection(self):
        import threading
        from unittest.mock import patch
        from transfer_core import Cancelled

        for category, extensions in [('image', ['jpg']), ('video', ['mp4']),
                                     ('recent', ['jpg', 'mp4', 'txt', 'mp3', 'zip'])]:
            with self.subTest(category=category):
                gates = [threading.Event() for _ in range(3)]
                requested = []

                class PreviewPhone:
                    def __init__(self, settings, password, cancel):
                        self.cancel = cancel
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def check_cancel(self):
                        if self.cancel.is_set():
                            raise Cancelled('cancelled')
                    def thumbnail(self, entry):
                        requested.append(entry.path)
                        gate = gates[int(entry.name[2:5]) // 60]
                        while not gate.wait(.01):
                            self.check_cancel()
                        return None

                def wait_for(predicate):
                    deadline = time.monotonic() + 5
                    while not predicate() and time.monotonic() < deadline:
                        self.app.processEvents()
                        QTest.qWait(10)
                    self.assertTrue(predicate())

                with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, LOCALAPPDATA=temp), patch('desktop.Phone', PreviewPhone):
                    window = Window(auto_discover=False)
                    try:
                        now = time.time() - 60
                        entries = [Entry(f'/Download/项目{i:03d}.{extensions[i % len(extensions)]}',
                                         f'项目{i:03d}.{extensions[i % len(extensions)]}', 52022, now - i)
                                   for i in range(125)]
                        expected_first = sum(e.kind in {'image', 'video'} for e in entries[:60])
                        expected_second = sum(e.kind in {'image', 'video'} for e in entries[:120])
                        window.connected = True
                        window.index = entries
                        window.show()
                        window.choose_category(category)
                        wait_for(lambda: len(requested) == 1)
                        self.assertEqual(window.scroll_loaded, 60)
                        self.assertTrue(window.previous.isHidden())
                        self.assertTrue(window.next.isHidden())
                        self.assertEqual(window.all_button.text(), '全选已加载')
                        first_gallery = window.galleries[0]
                        first_gallery.item(0).setSelected(True)
                        selected = window.selected()
                        batch = window.scroll_batches[-1]
                        bar = window.scroll.verticalScrollBar()
                        threshold = int(batch.y() + batch.height() / 2 - window.scroll.viewport().height())
                        self.assertGreater(threshold, 5)
                        bar.setValue(threshold - 5)
                        gates[0].set()
                        wait_for(lambda: not window.batch_loading)
                        self.assertEqual(window.scroll_loaded, 60)
                        self.assertEqual(len(requested), expected_first)
                        bar.setValue(threshold + 5)
                        wait_for(lambda: window.scroll_loaded == 120 and len(requested) == expected_first + 1)
                        self.assertIs(window.galleries[0], first_gallery)
                        self.assertEqual(window.selected(), selected)
                        self.assertEqual(bar.value(), threshold + 5)
                        for _ in range(5):
                            window.maybe_load_more()
                        self.assertEqual(window.scroll_loaded, 120)
                        gates[1].set()
                        wait_for(lambda: not window.batch_loading)
                        self.assertEqual(len(requested), expected_second)
                        QTest.qWait(30)
                        self.assertEqual(window.scroll_loaded, 120)
                        batch = window.scroll_batches[-1]
                        bar.setValue(int(batch.y() + batch.height() / 2 - window.scroll.viewport().height()) + 5)
                        wait_for(lambda: window.scroll_loaded == 125 and len(requested) == expected_second + 1)
                        self.assertEqual(sum(g.count() for g in window.galleries), 125)
                        window.search.setText('项目000')
                        wait_for(lambda: not window.batch_loading)
                        self.assertEqual(window.scroll_loaded, 1)
                        self.assertEqual(sum(g.count() for g in window.galleries), 1)
                        self.assertEqual(bar.value(), 0)
                        self.assertIn('已全部加载', window.page_label.text())
                        window.sort.setCurrentIndex(1)
                        wait_for(lambda: not window.batch_loading)
                        # The directory browser keeps its existing page buttons.
                        window.category = 'files'
                        window.render()
                        self.assertFalse(window.next.isHidden())
                    finally:
                        for gate in gates:
                            gate.set()
                        window.close()
                        self.app.processEvents()

    def test_non_media_scroll_without_network_and_resets_on_filter_and_sort(self):
        from unittest.mock import patch
        for category, extension in [('audio', 'mp3'), ('document', 'zip'), ('recent', 'txt')]:
            with self.subTest(category=category), tempfile.TemporaryDirectory() as temp, \
                    patch.dict(os.environ, LOCALAPPDATA=temp), \
                    patch('desktop.Phone', side_effect=AssertionError('No thumbnail connection expected')) as phone:
                window = Window(auto_discover=False)
                try:
                    now = time.time() - 60
                    entries = [Entry(f'/Download/文件{i:03d}.{extension}', f'文件{i:03d}.{extension}', 52022, now - i)
                               for i in range(125)]
                    if category == 'recent':
                        entries += [Entry('/old.txt', 'old.txt', 1, now - 31 * 86400),
                                    Entry('/future.txt', 'future.txt', 1, now + 86400)]
                    else:
                        entries.append(Entry('/other.jpg', 'other.jpg', 1, now))
                    window.connected = True
                    window.index = entries
                    window.show()
                    window.choose_category(category)
                    QTest.qWait(50)
                    self.assertEqual(len(window.filtered()), 125)
                    self.assertEqual(window.scroll_loaded, 60)
                    self.assertFalse(window.batch_loading)
                    self.assertFalse(window.tasks)
                    self.assertTrue(window.next.isHidden())
                    self.assertIn('60 / 125 项', window.page_label.text())
                    first_gallery = window.galleries[0]
                    first_gallery.item(0).setSelected(True)
                    selected = window.selected()
                    batch = window.scroll_batches[-1]
                    bar = window.scroll.verticalScrollBar()
                    threshold = int(batch.y() + batch.height() / 2 - window.scroll.viewport().height())
                    bar.setValue(threshold - 5)
                    QTest.qWait(30)
                    self.assertEqual(window.scroll_loaded, 60)
                    bar.setValue(threshold + 5)
                    # Scroll events can burst before Qt has positioned the appended widget.
                    for _ in range(15):
                        window.maybe_load_more()
                    self.assertEqual(window.scroll_loaded, 120)
                    QTest.qWait(50)
                    self.assertEqual(window.scroll_loaded, 120)
                    self.assertEqual(window.selected(), selected)
                    self.assertIs(window.galleries[0], first_gallery)
                    self.assertEqual(bar.value(), threshold + 5)
                    for _ in range(5):
                        window.maybe_load_more()
                    QTest.qWait(30)
                    self.assertEqual(window.scroll_loaded, 120)
                    batch = window.scroll_batches[-1]
                    bar.setValue(int(batch.y() + batch.height() / 2 - window.scroll.viewport().height()) + 5)
                    QTest.qWait(50)
                    self.assertEqual(window.scroll_loaded, 125)
                    self.assertIn('已全部加载', window.page_label.text())
                    window.search.setText('不存在')
                    QTest.qWait(30)
                    self.assertEqual(window.scroll_loaded, 0)
                    self.assertIn('已加载 0 / 0 项', window.page_label.text())
                    window.search.clear()
                    window.sort.setCurrentIndex(1)
                    QTest.qWait(30)
                    self.assertEqual(window.scroll_loaded, 60)
                    self.assertEqual(window.filtered()[0].name, f'文件124.{extension}')
                    self.assertEqual(bar.value(), 0)
                    self.assertFalse(window.selected())
                    phone.assert_not_called()
                finally:
                    window.close()
                    self.app.processEvents()

    def test_auto_pair_reconnect_and_phone_configured_upload_directory(self):
        from unittest.mock import patch
        from discovery import identity
        from test_transfer import TransferTests
        import json
        fixture = TransferTests('test_thumbnail_and_names')
        TransferTests.setUpClass()
        fixture.setUp()
        window = None
        try:
            phone_root = fixture.remote / 'storage/emulated/0'
            receiver = phone_root / 'Download/互传'
            receiver.mkdir(parents=True)
            state = phone_root / '.lc-transfer'
            state.mkdir()
            (state / 'device.json').write_text(json.dumps({'receive_dir': '/storage/emulated/0/Download/互传'}))
            import gzip
            (state / 'catalog.json.gz').write_bytes(gzip.compress(json.dumps({'version': 1, 'files': [], 'truncated': False}).encode()))
            fixture.public_key = identity()
            candidate = {'host': '127.0.0.1', 'port': fixture.settings.port, 'name': '测试手机',
                         'paired': True, 'fingerprint': fixture.settings.fingerprint}
            with patch('desktop.discover', return_value=[candidate]), patch('desktop.QMessageBox.warning') as warning:
                window = Window(auto_discover=False)
                window.start_thumbnails = lambda *args: None
                window.settings = fixture.settings
                window.auto_enabled = True
                window.show()

                def wait_for(predicate):
                    deadline = time.monotonic() + 30
                    while not predicate() and time.monotonic() < deadline:
                        self.app.processEvents()
                        QTest.qWait(10)
                    self.assertTrue(predicate(), f'connected={window.connected}, busy={window.busy}, status={window.status.text()}, workers={window.pool.activeThreadCount()}')
                    warning.assert_not_called()

                window.auto_tick()
                wait_for(lambda: window.connected and not window.busy)
                self.assertTrue(window.settings.key_auth)
                self.assertEqual(window.category, 'recent')
                self.assertTrue(window.nav['recent'].isChecked())
                self.assertEqual(window.settings.receive_dir, '/storage/emulated/0/Download/互传')
                self.assertIsNotNone(window.control)
                self.assertTrue((state / 'session.json').exists())
                source = fixture.base / '发送.zip'
                source.write_bytes(b'archive bytes')
                window.transfer('upload', [str(source)])
                wait_for(lambda: not window.busy)
                self.assertEqual((receiver / source.name).read_bytes(), b'archive bytes')
                self.assertEqual(window.history.snapshot()['jobs'][0]['state'], 'done')
                window.control.ssh.close()
                window.auto_tick()
                wait_for(lambda: not window.connected and not window.maintenance_running)
                window.retry_at = 0
                window.auto_tick()
                wait_for(lambda: window.connected and not window.busy)
                window.toggle_connection()
                self.assertFalse(window.connected)
                self.assertFalse(window.auto_enabled)
        finally:
            if window is not None:
                window.busy = False
                if window.transfer_dialog:
                    window.transfer_dialog.close()
                window.close()
                self.app.processEvents()
            fixture.tearDown()

    def test_lan_failure_falls_back_and_idle_handover_keeps_selection(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from transfer_core import Settings
        FALLBACK_HOST = '198.51.100.2'  # RFC 5737 fixture only.
        lan = '192.0.2.42'
        fingerprint = 'SHA256:' + 'A' * 43
        state = {'reachable': False, 'wrong_identity': False}
        attempted = []
        entry = Entry('/storage/emulated/0/photo.jpg', 'photo.jpg', 100, time.time())

        class NetworkPhone:
            def __init__(self, settings, *args):
                self.settings, self.root, self.closed = settings, settings.root, False
                self.ssh = SimpleNamespace(close=lambda: setattr(self, 'closed', True))
                self.sftp = SimpleNamespace(get_channel=lambda: SimpleNamespace(settimeout=lambda value: None))
            def __enter__(self):
                attempted.append(self.settings.host)
                if self.settings.host == lan and not state['reachable']:
                    raise ConnectionRefusedError('LAN unavailable')
                return self
            def __exit__(self, *args): self.ssh.close()
            def list_dir(self, root): return root, [entry]
            def scan(self, report): return [entry], [], False
            def device_config(self): return '/storage/emulated/0/Download/互传'
            def pending_share(self): return None
            def heartbeat(self):
                if self.closed or (self.settings.host == lan and not state['reachable']):
                    raise ConnectionResetError('connection lost')

        def phones(host, key, *args, **kwargs):
            local = {'host': lan, 'port': 52022, 'name': '测试手机', 'paired': True,
                     'fingerprint': 'SHA256:' + 'B' * 43 if state['wrong_identity'] else fingerprint}
            overlay = dict(local, host=FALLBACK_HOST, fingerprint=fingerprint)
            return [local] if kwargs.get('lan_only') else [local, overlay]

        def wait_for(predicate):
            deadline = time.monotonic() + 5
            while not predicate() and time.monotonic() < deadline:
                self.app.processEvents()
                QTest.qWait(10)
            self.assertTrue(predicate())

        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, LOCALAPPDATA=temp), \
                patch('desktop.Phone', NetworkPhone), patch('desktop.discover', side_effect=phones) as discovery, \
                patch('desktop.is_lan_address', side_effect=lambda host: host == lan), \
                patch('desktop.QMessageBox.warning') as warning:
            window = Window(auto_discover=False)
            try:
                window.settings = Settings(host=FALLBACK_HOST, fingerprint=fingerprint, key_auth=True, fallback_host=FALLBACK_HOST)
                window.start_thumbnails = lambda *args: None
                window.auto_enabled = True
                window.show()
                window.auto_tick()
                wait_for(lambda: window.connected and not window.busy)
                self.assertEqual(attempted, [lan, FALLBACK_HOST, FALLBACK_HOST])
                self.assertEqual(window.settings.host, FALLBACK_HOST)
                galleries = window.galleries
                galleries[0].item(0).setSelected(True)
                old_control = window.control
                state['reachable'] = True
                window.lan_check_at = 0
                window.busy = True
                calls = discovery.call_count
                window.auto_tick()
                wait_for(lambda: not window.maintenance_running)
                self.assertEqual(discovery.call_count, calls)
                self.assertEqual(window.settings.host, FALLBACK_HOST)
                window.busy = False
                state['wrong_identity'] = True
                window.auto_tick()
                wait_for(lambda: not window.maintenance_running)
                self.assertEqual(window.settings.host, FALLBACK_HOST)
                state['wrong_identity'] = False
                window.lan_check_at = 0
                window.auto_tick()
                wait_for(lambda: window.settings.host == lan and not window.maintenance_running)
                self.assertIs(window.galleries, galleries)
                self.assertEqual(window.selected(), [entry])
                self.assertTrue(old_control.closed)
                self.assertEqual(window.settings.fingerprint, fingerprint)
                state['reachable'] = False
                window.auto_tick()
                wait_for(lambda: not window.connected and not window.maintenance_running)
                window.retry_at = 0
                window.auto_tick()
                wait_for(lambda: window.connected and not window.busy)
                self.assertEqual(window.settings.host, FALLBACK_HOST)
                warning.assert_not_called()
            finally:
                window.busy = False
                window.close()
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
