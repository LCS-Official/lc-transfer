"""Original image viewing through the real desktop and SFTP pipeline."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop import Entry, Window
from transfer_core import Cancelled


class ImagePreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')

    def wait_for(self, predicate):
        deadline = time.monotonic() + 15
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertTrue(predicate())

    def test_double_click_loads_full_original_in_client_with_orientation_and_zoom(self):
        from test_transfer import TransferTests
        fixture = TransferTests('test_thumbnail_and_names')
        TransferTests.setUpClass()
        fixture.setUp()
        window = None
        temps = []
        temporary_directory = tempfile.TemporaryDirectory

        def preview_temp(**kwargs):
            folder = temporary_directory(dir=fixture.base, **kwargs)
            temps.append(Path(folder.name))
            return folder

        try:
            original = fixture.remote / '原始照片.jpg'
            image = Image.new('RGB', (1800, 1200), '#50a4c0')
            exif = Image.Exif()
            exif[274] = 6
            image.save(original, quality=96, exif=exif)
            with patch('desktop.tempfile.TemporaryDirectory', side_effect=preview_temp), \
                    patch('desktop.QDesktopServices.openUrl') as external, \
                    patch('desktop.QMessageBox.warning') as warning:
                window = Window(auto_discover=False)
                window.settings, window.password = fixture.settings, 'test-password'
                window.connected = True
                window.start_thumbnails = lambda *args: None
                window.entries = [Entry('/' + original.name, original.name,
                                        original.stat().st_size, original.stat().st_mtime)]
                window.show()
                window.render()
                self.app.processEvents()
                gallery = window.galleries[0]
                item = gallery.item(0)
                point = gallery.visualItemRect(item).center()
                QTest.mouseClick(gallery.viewport(), Qt.MouseButton.LeftButton, pos=point)
                QTest.mouseDClick(gallery.viewport(), Qt.MouseButton.LeftButton, pos=point)
                self.wait_for(lambda: window.image_preview is not None and not window.busy)
                preview = window.image_preview
                self.assertTrue(preview.isVisible())
                pixmap = preview.view.scene().items()[0].pixmap()
                self.assertEqual((pixmap.width(), pixmap.height()), (1200, 1800))
                self.assertEqual(pixmap.toImage().pixelColor(200, 300).alpha(), 255)
                self.assertIn('原图', preview.info.text())
                self.assertTrue(all(button.isEnabled() for button in preview.zoom_buttons))
                fitted_scale = preview.view.transform().m11()
                QTest.mouseClick(preview.zoom_buttons[1], Qt.MouseButton.LeftButton)
                self.assertAlmostEqual(preview.view.transform().m11() * preview.devicePixelRatioF(), 1)
                QTest.mouseClick(preview.zoom_buttons[3], Qt.MouseButton.LeftButton)
                self.assertAlmostEqual(preview.view.transform().m11() * preview.devicePixelRatioF(), 1.25)
                QTest.mouseClick(preview.zoom_buttons[0], Qt.MouseButton.LeftButton)
                self.assertAlmostEqual(preview.view.transform().m11(), fitted_scale, places=2)
                self.assertIn(('open', '/' + original.name), fixture.requests)
                self.assertFalse(any('/previews/' in path for action, path in fixture.requests))
                self.assertFalse(Path(fixture.settings.download_dir).exists())
                self.assertFalse(window.history.snapshot()['jobs'])
                self.assertTrue(temps)
                self.assertTrue(all(not folder.exists() for folder in temps))
                external.assert_not_called()
                warning.assert_not_called()
                preview.close()
                self.app.processEvents()
                self.assertIsNone(window.image_preview)
        finally:
            if window is not None:
                window.cancel.set()
                self.wait_for(lambda: not window.busy)
                if window.image_preview:
                    window.image_preview.close()
                window.close()
                self.app.processEvents()
            fixture.tearDown()

    def test_closing_loading_preview_cancels_and_cleans_temporary_original(self):
        started = threading.Event()
        folders = []

        class SlowPhone:
            def __init__(self, settings, password, cancel):
                self.cancel = cancel
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def download(self, entries, destination, report):
                folders.append(destination)
                (destination / '.partial.part').write_bytes(b'partial original')
                started.set()
                if not self.cancel.wait(5):
                    raise AssertionError('Closing did not cancel the preview')
                raise Cancelled('cancelled')

        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, LOCALAPPDATA=temp), \
                patch('desktop.Phone', SlowPhone), patch('desktop.QMessageBox.warning') as warning:
            window = Window(auto_discover=False)
            try:
                window.connected = True
                window.show()
                window.preview_image(Entry('/photo.jpg', 'photo.jpg', 100, time.time()))
                self.wait_for(started.is_set)
                preview = window.image_preview
                self.assertTrue(window.busy)
                preview.reject()
                self.assertIsNone(window.image_preview)
                self.wait_for(lambda: not window.busy and not window.tasks)
                self.assertTrue(folders)
                self.assertTrue(all(not folder.exists() for folder in folders))
                self.assertIn('已关闭', window.status.text())
                warning.assert_not_called()
            finally:
                window.cancel.set()
                self.wait_for(lambda: not window.busy)
                window.close()
                self.app.processEvents()

    def test_invalid_image_stays_in_client_and_cleans_original(self):
        folders = []

        class InvalidPhone:
            def __init__(self, *args): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def check_cancel(self): pass
            def download(self, entries, destination, report):
                folders.append(destination)
                (destination / 'bad.jpg').write_bytes(b'invalid image bytes')

        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, LOCALAPPDATA=temp), \
                patch('desktop.Phone', InvalidPhone), \
                patch('desktop.QDesktopServices.openUrl') as external, \
                patch('desktop.QMessageBox.warning') as warning:
            window = Window(auto_discover=False)
            try:
                window.connected = True
                window.show()
                window.preview_image(Entry('/bad.jpg', 'bad.jpg', 19, time.time()))
                self.wait_for(lambda: not window.busy)
                preview = window.image_preview
                self.assertTrue(preview.isVisible())
                self.assertIn('格式不受支持或文件损坏', preview.info.text())
                self.assertTrue(preview.progress.isHidden())
                self.assertTrue(all(not button.isEnabled() for button in preview.zoom_buttons))
                self.assertTrue(all(not folder.exists() for folder in folders))
                external.assert_not_called()
                warning.assert_not_called()
                preview.close()
                self.app.processEvents()
            finally:
                window.cancel.set()
                self.wait_for(lambda: not window.busy)
                window.close()
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
