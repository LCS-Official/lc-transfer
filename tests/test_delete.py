"""Phone file deletion over real SFTP and the desktop confirmation flow."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import stat
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import test_transfer
from desktop import Window
from transfer_core import Cancelled, Entry, Phone


class DeleteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        test_transfer.TransferTests.setUpClass()

    def setUp(self):
        self.fixture = test_transfer.TransferTests('test_thumbnail_and_names')
        self.fixture.setUp()
        self.window = None

    def tearDown(self):
        if self.window:
            self.window.busy = False
            self.window.close()
            self.app.processEvents()
        self.fixture.tearDown()

    def entry(self, relative):
        path = self.fixture.remote / relative
        attr = path.stat()
        return Entry('/' + relative, path.name, attr.st_size, int(attr.st_mtime), path.is_dir())

    def test_only_selected_files_are_deleted_and_duplicates_are_deduplicated(self):
        for name in ['照片.jpg', '视频.mp4', '保留.txt']:
            (self.fixture.remote / name).write_bytes(name.encode())
        entries = [self.entry('照片.jpg'), self.entry('视频.mp4')]
        reports = []
        with self.fixture.phone() as phone:
            removed = phone.delete_files(entries + entries[:1], lambda *args: reports.append(args))
        self.assertEqual(removed, [e.path for e in entries])
        self.assertEqual(reports, [(entries[0].path, 1, 2), (entries[1].path, 2, 2)])
        self.assertFalse((self.fixture.remote / '照片.jpg').exists())
        self.assertFalse((self.fixture.remote / '视频.mp4').exists())
        self.assertEqual((self.fixture.remote / '保留.txt').read_bytes(), '保留.txt'.encode())

    def test_boundaries_directories_state_symlinks_and_changed_files_are_rejected(self):
        root = self.fixture.remote / 'shared'
        (root / '.lc-transfer').mkdir(parents=True)
        (root / '.lc-transfer/history.json').write_bytes(b'{}')
        (root / 'folder').mkdir()
        (root / 'folder/child.txt').write_bytes(b'keep child')
        (root / 'safe.txt').write_bytes(b'safe bytes')
        (self.fixture.remote / 'outside.txt').write_bytes(b'outside')
        safe = self.entry('shared/safe.txt')
        with self.fixture.phone() as phone:
            phone.root = '/shared'
            for invalid in [self.entry('outside.txt'), self.entry('shared/folder'),
                            self.entry('shared/.lc-transfer/history.json'), self.entry('shared')]:
                with self.subTest(path=invalid.path), self.assertRaises(ValueError):
                    phone.delete_files([safe, invalid])
                self.assertTrue((root / 'safe.txt').exists())
            from paramiko import SFTPAttributes
            attrs = SFTPAttributes()
            attrs.st_mode = stat.S_IFLNK
            with patch.object(phone.sftp, 'lstat', return_value=attrs), self.assertRaises(ValueError):
                phone.delete_files([safe])
            (root / 'safe.txt').write_bytes(b'changed bytes')
            with self.assertRaisesRegex(ValueError, '发生变化'):
                phone.delete_files([safe])
        self.assertEqual((root / 'folder/child.txt').read_bytes(), b'keep child')
        self.assertEqual((root / '.lc-transfer/history.json').read_bytes(), b'{}')
        self.assertTrue((self.fixture.remote / 'outside.txt').exists())

    def test_cancel_or_permission_failure_preserves_remaining_files(self):
        for mode in ['cancel', 'permission']:
            with self.subTest(mode=mode):
                for name in ['first.txt', 'second.txt']:
                    (self.fixture.remote / name).write_bytes(b'keep unless selected')
                entries = [self.entry('first.txt'), self.entry('second.txt')]
                reports = []
                with self.fixture.phone() as phone:
                    remove = phone.sftp.remove
                    def guarded_remove(path):
                        if mode == 'permission' and path == entries[1].path:
                            raise PermissionError('read-only file')
                        return remove(path)
                    def report(path, count, total):
                        reports.append(path)
                        if mode == 'cancel':
                            phone.cancel.set()
                    with patch.object(phone.sftp, 'remove', side_effect=guarded_remove):
                        with self.assertRaises(Cancelled if mode == 'cancel' else PermissionError):
                            phone.delete_files(entries, report)
                self.assertEqual(reports, [entries[0].path])
                self.assertFalse((self.fixture.remote / 'first.txt').exists())
                self.assertTrue((self.fixture.remote / 'second.txt').exists())

    def setup_window(self, entries):
        w = self.window = Window(auto_discover=False)
        w.settings, w.password = self.fixture.settings, 'test-password'
        w.start_thumbnails = lambda *args: None
        w.connected = True
        w.index = list(entries)
        w.show()
        w.choose_category('recent')
        self.app.processEvents()
        return w

    def answer_confirmation(self, answer):
        seen = []
        def respond():
            box = self.app.activeModalWidget()
            if isinstance(box, QMessageBox):
                seen.append((box.windowTitle(), box.text(), box.informativeText(),
                             box.defaultButton() == box.button(QMessageBox.StandardButton.No)))
                box.button(answer).click()
        QTimer.singleShot(0, respond)
        self.window.delete_button.click()
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0][3], 'Cancel must be the default')
        self.assertIn('永久删除', seen[0][1])
        return seen[0]

    def wait_finished(self):
        deadline = time.monotonic() + 15
        while self.window.busy and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertFalse(self.window.busy)

    def test_confirmation_cancel_and_accept_delete_only_the_selection(self):
        for name in ['待删除.zip', '保留.txt']:
            (self.fixture.remote / name).write_bytes(b'test file')
        entries = [self.entry('待删除.zip'), self.entry('保留.txt')]
        w = self.setup_window(entries)
        self.assertFalse(w.delete_button.isEnabled())
        gallery = w.galleries[0]
        next(gallery.item(i) for i in range(gallery.count())
             if gallery.item(i).data(Qt.ItemDataRole.UserRole).name == '待删除.zip').setSelected(True)
        self.assertTrue(w.delete_button.isEnabled())
        message = self.answer_confirmation(QMessageBox.StandardButton.No)
        self.assertIn('待删除.zip', message[2])
        self.assertTrue((self.fixture.remote / '待删除.zip').exists())
        self.assertFalse(w.busy)
        with patch('desktop.QMessageBox.warning') as warning:
            self.answer_confirmation(QMessageBox.StandardButton.Yes)
            self.wait_finished()
            warning.assert_not_called()
        self.assertFalse((self.fixture.remote / '待删除.zip').exists())
        self.assertTrue((self.fixture.remote / '保留.txt').exists())
        self.assertEqual([e.name for e in w.entries], ['保留.txt'])
        self.assertEqual([e.name for e in w.index], ['保留.txt'])
        self.assertFalse(w.selected())
        self.assertFalse(w.delete_button.isEnabled())
        self.assertIn('删除 1 个文件', w.status.text())
        folder = self.fixture.remote / 'folder'
        folder.mkdir()
        w.entries = [self.entry('folder')]
        w.category = 'files'
        w.render()
        w.galleries[0].item(0).setSelected(True)
        self.assertFalse(w.delete_button.isEnabled())
        with patch('desktop.QMessageBox.exec') as confirmation:
            w.delete_selected()
            confirmation.assert_not_called()

    def test_partial_failure_removes_completed_rows_and_reports_the_remainder(self):
        for name in ['a.txt', 'b.txt']:
            (self.fixture.remote / name).write_bytes(b'test file')
        entries = [self.entry('a.txt'), self.entry('b.txt')]
        class FailingPhone(Phone):
            def delete_files(self, chosen, report=lambda *args: None):
                remove = self.sftp.remove
                def guarded_remove(path):
                    if path == '/b.txt':
                        raise PermissionError('permission denied for b.txt')
                    return remove(path)
                with patch.object(self.sftp, 'remove', side_effect=guarded_remove):
                    return super().delete_files(chosen, report)
        w = self.setup_window(entries)
        w.select_all()
        with patch('desktop.Phone', FailingPhone), patch('desktop.QMessageBox.warning') as warning:
            self.answer_confirmation(QMessageBox.StandardButton.Yes)
            self.wait_finished()
            warning.assert_called_once()
            self.assertIn('1 / 2', warning.call_args.args[2])
            self.assertIn('permission denied', warning.call_args.args[2])
        self.assertFalse((self.fixture.remote / 'a.txt').exists())
        self.assertTrue((self.fixture.remote / 'b.txt').exists())
        self.assertEqual([e.name for e in w.entries], ['b.txt'])
        self.assertEqual([e.name for e in w.index], ['b.txt'])


if __name__ == '__main__':
    unittest.main()
