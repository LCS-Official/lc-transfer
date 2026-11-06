"""Phone-initiated share delivery through the existing paired SFTP connection."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
import stat
import time
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import test_transfer
from desktop import Window
from discovery import identity
from transfer_core import Phone


class SystemShareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        test_transfer.TransferTests.setUpClass()

    def setUp(self):
        self.fixture = test_transfer.TransferTests('test_thumbnail_and_names')
        self.fixture.setUp()
        self.fixture.public_key = identity()
        self.settings = replace(self.fixture.settings, key_auth=True)
        self.window = None
        (self.fixture.remote / '.lc-transfer').mkdir()

    def tearDown(self):
        if self.window:
            if self.window.transfer_dialog:
                self.window.transfer_dialog.close()
            self.window.busy = False
            self.window.close()
            self.app.processEvents()
        self.fixture.tearDown()

    def stage(self, names=('应用.apk', '说明.txt')):
        folder = self.fixture.remote / '.lc-transfer/outbox' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        rows = []
        payloads = []
        for index, name in enumerate(names):
            payload = ('original ' + name + str(index)).encode()
            source = folder / str(index)
            source.write_bytes(payload)
            rows.append(dict(file=str(index), name=name, size=len(payload), mtime=int(source.stat().st_mtime)))
            payloads.append(payload)
        manifest = dict(version=1, id=folder.name, files=rows)
        (folder / 'request.json').write_text(json.dumps(manifest), encoding='utf-8')
        return folder, manifest, payloads

    def create_window(self):
        w = self.window = Window(auto_discover=False)
        w.settings = self.settings
        w.computer_key = self.fixture.public_key
        w.control = Phone(self.settings, '')
        w.control.__enter__()
        w.connected = True
        w.start_thumbnails = lambda *args: None
        w.show()
        w.update_controls()
        return w

    def wait_for(self, predicate):
        until = time.monotonic() + 15
        while not predicate() and time.monotonic() < until:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertTrue(predicate())

    def test_pending_rejects_bad_metadata_paths_types_and_password_login(self):
        folder, manifest, _ = self.stage()
        with self.fixture.phone() as legacy:
            self.assertIsNone(legacy.pending_share())
        variants = [dict(manifest, id='bad'), dict(manifest, version=2), dict(manifest, files=[])]
        for field, value in [('file','../outside'), ('name','../outside'), ('name','/absolute'),
                             ('size',-1), ('size',True), ('mtime',float('inf'))]:
            rows = [dict(manifest['files'][0], **{field:value})]
            variants.append(dict(manifest, files=rows))
        with Phone(self.settings, '') as phone:
            for variant in variants:
                (folder/'request.json').write_text(json.dumps(variant),encoding='utf-8')
                with self.subTest(variant=variant), self.assertRaises(ValueError):
                    phone.pending_share()
            (folder/'request.json').write_text(json.dumps(manifest),encoding='utf-8')
            original = phone.sftp.lstat
            def symlink(path):
                attr=original(path)
                if path.endswith('/0'): attr.st_mode=stat.S_IFLNK
                return attr
            with patch.object(phone.sftp,'lstat',side_effect=symlink), self.assertRaises(ValueError):
                phone.pending_share()
        self.assertTrue((folder/'0').exists())

    def test_idle_poll_receives_originals_and_mirrors_progress_without_overwriting(self):
        folder, manifest, payloads = self.stage(('应用.apk','应用.apk','说明.txt'))
        receiver=Path(self.settings.download_dir)
        receiver.mkdir()
        (receiver/'应用.apk').write_bytes(b'existing local APK')
        w=self.create_window()
        w.busy=True
        w.auto_tick()
        self.wait_for(lambda:not w.maintenance_running)
        self.assertFalse(w.history.snapshot()['jobs'])
        self.assertTrue(folder.exists())
        w.busy=False
        with patch('desktop.QMessageBox.warning') as warning:
            w.auto_tick()
            self.wait_for(lambda: bool(w.history.snapshot()['jobs']) and not w.busy and not w.maintenance_running)
            warning.assert_not_called()
        job=w.history.snapshot()['jobs'][0]
        self.assertEqual(job['state'],'done')
        self.assertEqual(job['share_id'],folder.name)
        self.assertEqual(job['archive'],'')
        self.assertEqual((receiver/'应用.apk').read_bytes(),b'existing local APK')
        self.assertEqual((receiver/'应用 (1).apk').read_bytes(),payloads[0])
        self.assertEqual((receiver/'应用 (2).apk').read_bytes(),payloads[1])
        self.assertEqual((receiver/'说明.txt').read_bytes(),payloads[2])
        self.assertFalse(list(receiver.glob('*.zip')))
        self.assertFalse(folder.exists())
        mirror=json.loads((self.fixture.remote/'.lc-transfer/history.json').read_text(encoding='utf-8'))
        self.assertEqual(mirror['jobs'][0]['state'],'done')
        self.assertEqual(len(mirror['jobs'][0]['files']),3)
        self.assertTrue(all(item['state']=='done' for item in mirror['jobs'][0]['files']))

    def test_restart_acknowledges_completed_share_without_redownloading(self):
        folder, manifest, payloads=self.stage(('应用.apk',))
        w=self.create_window()
        with patch.object(Phone,'complete_share',side_effect=OSError('temporary acknowledgement loss')):
            w.auto_tick()
            self.wait_for(lambda: bool(w.history.snapshot()['jobs']) and not w.busy and not w.maintenance_running)
        self.assertEqual(w.history.snapshot()['jobs'][0]['state'],'done')
        self.assertTrue(folder.exists())
        w.transfer_dialog.close()
        w.close()
        self.app.processEvents()
        w=self.create_window()
        with patch.object(Phone,'download',side_effect=AssertionError('must not download twice')):
            w.auto_tick()
            self.wait_for(lambda:not folder.exists() and not w.busy and not w.maintenance_running)
        self.assertEqual(len(w.history.snapshot()['jobs']),1)
        receiver=Path(self.settings.download_dir)
        self.assertEqual([p.name for p in receiver.iterdir()],['应用.apk'])
        self.assertEqual((receiver/'应用.apk').read_bytes(),payloads[0])

    def test_acknowledged_partial_cleanup_is_resumed_without_delivery(self):
        folder, manifest, _=self.stage()
        (folder/'received').write_bytes(b'1')
        (folder/'0').unlink()
        with Phone(self.settings,'') as phone:
            self.assertIsNone(phone.pending_share())
        self.assertFalse(folder.exists())
        self.assertFalse(Path(self.settings.download_dir).exists())


if __name__=='__main__':
    unittest.main()
