"""Desktop drag/drop uploads through Qt event routing and real SFTP."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop import Entry, Window


class DropUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')

    def wait_finished(self, window):
        deadline = time.monotonic() + 30
        while window.busy and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertFalse(window.busy)

    def test_drop_on_gallery_uploads_files_and_folder_to_phone_receive_directory(self):
        from test_transfer import TransferTests
        fixture = TransferTests('test_thumbnail_and_names')
        TransferTests.setUpClass()
        fixture.setUp()
        window = None
        try:
            receiver = fixture.remote / 'Receive'
            receiver.mkdir()
            fixture.settings.receive_dir = '/Receive'
            (receiver / '报告.txt').write_bytes(b'existing phone file')
            elsewhere = fixture.remote / 'Elsewhere'
            elsewhere.mkdir()
            originals = {'报告.txt': '测试文件'.encode(), '视频.mp4': b'original video bytes'}
            for name, payload in originals.items():
                (fixture.base / name).write_bytes(payload)
            folder = fixture.base / '资料夹'
            folder.mkdir()
            (folder / '子文件.bin').write_bytes(b'child bytes')
            paths = [fixture.base / name for name in originals] + [folder]
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths + paths[:1]])
            with patch('desktop.QMessageBox.warning') as warning, \
                    patch('PySide6.QtWidgets.QInputDialog.getText') as prompt:
                window = Window(auto_discover=False)
                window.settings, window.password = fixture.settings, 'test-password'
                window.connected = True
                window.current_path = '/Elsewhere'
                window.start_thumbnails = lambda *args: None
                window.index = [Entry('/phone.jpg', 'phone.jpg', 1, time.time() - 60)]
                window.show()
                window.choose_category('recent')
                self.app.processEvents()
                target = window.galleries[0].viewport()
                actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction
                enter = QDragEnterEvent(QPoint(20, 20), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                enter.setDropAction(Qt.DropAction.MoveAction)
                QApplication.sendEvent(target, enter)
                self.assertTrue(enter.isAccepted())
                self.assertEqual(enter.dropAction(), Qt.DropAction.CopyAction)
                move = QDragMoveEvent(QPoint(25, 25), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                QApplication.sendEvent(target, move)
                self.assertTrue(move.isAccepted())
                drop = QDropEvent(QPointF(25, 25), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                drop.setDropAction(Qt.DropAction.MoveAction)
                QApplication.sendEvent(target, drop)
                self.assertTrue(drop.isAccepted())
                self.assertEqual(drop.dropAction(), Qt.DropAction.CopyAction)
                self.assertTrue(window.busy)
                self.assertIsNotNone(window.transfer_dialog)
                self.wait_finished(window)
                warning.assert_not_called()
                prompt.assert_not_called()
                self.assertEqual((receiver / '报告.txt').read_bytes(), b'existing phone file')
                self.assertEqual((receiver / '报告 (1).txt').read_bytes(), originals['报告.txt'])
                self.assertEqual((receiver / '视频.mp4').read_bytes(), originals['视频.mp4'])
                self.assertEqual((receiver / '资料夹/子文件.bin').read_bytes(), b'child bytes')
                self.assertFalse(list(elsewhere.iterdir()))
                for name, payload in originals.items():
                    self.assertEqual((fixture.base / name).read_bytes(), payload)
                self.assertEqual((folder / '子文件.bin').read_bytes(), b'child bytes')
                job = window.history.snapshot()['jobs'][0]
                self.assertEqual(job['direction'], 'upload')
                self.assertEqual(job['state'], 'done')
                self.assertEqual(job['target'], '/Receive')
                self.assertEqual(len(job['selected']), 3)
                self.assertEqual(len(job['files']), 3)
                self.assertTrue((fixture.remote / '.lc-transfer/history.json').exists())
                window.transfer_dialog.refresh()
                self.assertEqual(window.transfer_dialog.tree.topLevelItem(0).text(2), '已完成')
        finally:
            if window is not None:
                window.cancel.set()
                self.wait_finished(window)
                if window.transfer_dialog:
                    window.transfer_dialog.close()
                window.close()
                self.app.processEvents()
            fixture.tearDown()

    def test_drop_rejects_remote_text_relative_paths_move_only_and_busy_state(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, LOCALAPPDATA=temp):
            source = Path(temp) / 'test.txt'
            source.write_bytes(b'source bytes')
            local = QMimeData()
            local.setUrls([QUrl.fromLocalFile(str(source))])
            remote = QMimeData()
            remote.setUrls([QUrl('https://example.com/file.jpg')])
            mixed = QMimeData()
            mixed.setUrls(local.urls() + remote.urls())
            relative = QMimeData()
            relative.setUrls([QUrl.fromLocalFile('relative.txt')])
            text = QMimeData()
            text.setText(str(source))
            empty = QMimeData()
            window = Window(auto_discover=False)
            window.show()
            try:
                with patch.object(window, 'transfer') as upload:
                    for label, mime, connected, busy, closing, action in [
                            ('offline', local, False, False, False, Qt.DropAction.CopyAction),
                            ('busy', local, True, True, False, Qt.DropAction.CopyAction),
                            ('closing', local, True, False, True, Qt.DropAction.CopyAction),
                            ('remote', remote, True, False, False, Qt.DropAction.CopyAction),
                            ('mixed', mixed, True, False, False, Qt.DropAction.CopyAction),
                            ('relative', relative, True, False, False, Qt.DropAction.CopyAction),
                            ('text', text, True, False, False, Qt.DropAction.CopyAction),
                            ('empty', empty, True, False, False, Qt.DropAction.CopyAction),
                            ('move', local, True, False, False, Qt.DropAction.MoveAction)]:
                        with self.subTest(case=label):
                            window.connected, window.busy, window.closing = connected, busy, closing
                            enter = QDragEnterEvent(QPoint(20, 20), action, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                            QApplication.sendEvent(window, enter)
                            self.assertFalse(enter.isAccepted())
                            drop = QDropEvent(QPointF(20, 20), action, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                            QApplication.sendEvent(window, drop)
                            self.assertFalse(drop.isAccepted())
                    window.connected, window.busy, window.closing = True, False, False
                    enter = QDragEnterEvent(QPoint(20, 20), Qt.DropAction.CopyAction, local, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                    QApplication.sendEvent(window, enter)
                    self.assertTrue(enter.isAccepted())
                    window.busy = True
                    drop = QDropEvent(QPointF(20, 20), Qt.DropAction.CopyAction, local, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                    QApplication.sendEvent(window, drop)
                    self.assertFalse(drop.isAccepted())
                    upload.assert_not_called()
                    window.busy = False
                    for widget in [window.search, window.path, window.scroll.viewport(), window.folder_button]:
                        with self.subTest(drop_area=widget.objectName() or type(widget).__name__):
                            enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, local, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                            QApplication.sendEvent(widget, enter)
                            self.assertTrue(enter.isAccepted())
                            drop = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, local, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                            QApplication.sendEvent(widget, drop)
                            self.assertTrue(drop.isAccepted())
                            upload.assert_called_once()
                            args, kwargs = upload.call_args
                            self.assertEqual(args[0], 'upload')
                            self.assertEqual([Path(p) for p in args[1]], [source])
                            self.assertEqual(kwargs, {'choose_destination': False})
                            upload.reset_mock()
                    self.assertEqual(window.search.text(), '')
                    self.assertEqual(window.path.text(), window.current_path)
                    self.assertEqual(source.read_bytes(), b'source bytes')
            finally:
                window.busy, window.closing = False, False
                window.close()
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
