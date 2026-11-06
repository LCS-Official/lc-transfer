"""Immediate cancellation while TCP, SSH and SFTP peers stop responding."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import errno
import socket
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import paramiko
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop import Window
from transfer_core import CancelEvent, Cancelled, Phone, Settings
import test_transfer


class CancellationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        test_transfer.TransferTests.setUpClass()

    def wait_for(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(5)
        self.assertTrue(predicate())

    def test_tcp_connect_wait_is_cancelled_without_waiting_for_its_deadline(self):
        entered = threading.Event()
        cancel = CancelEvent()
        errors = []
        class PendingSocket:
            closed = False
            def setblocking(self, value): pass
            def connect_ex(self, address):
                entered.set()
                return errno.EINPROGRESS
            def shutdown(self, how): self.closed = True
            def close(self): self.closed = True
        sock = PendingSocket()
        def pending(*args):
            time.sleep(.01)
            return [], [], []
        def connect():
            try:
                with Phone(Settings(host='198.51.100.2'), '', cancel):
                    self.fail('Pending TCP connection must never succeed')
            except Exception as error:
                errors.append(error)
        with patch('transfer_core.socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('198.51.100.2',52022))]), \
                patch('transfer_core.socket.socket', return_value=sock), patch('transfer_core.select.select', side_effect=pending):
            worker = threading.Thread(target=connect)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                started = time.monotonic()
                cancel.set()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertLess(time.monotonic()-started, .5)
                self.assertTrue(sock.closed)
                self.assertIsInstance(errors[0], Cancelled)
            finally:
                cancel.set()
                worker.join(2)

    def test_cancel_silent_ssh_banner_pauses_auto_retry_and_allows_new_connection(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1',0))
        listener.listen()
        listener.settimeout(3)
        accepted = threading.Event()
        release = threading.Event()
        def silent_peer():
            try:
                connection, _ = listener.accept()
                with connection:
                    accepted.set()
                    release.wait(5)
            except OSError:
                pass
        server = threading.Thread(target=silent_peer)
        server.start()
        w = None
        fixture = test_transfer.TransferTests('test_thumbnail_and_names')
        fixture.setUp()
        try:
            with patch('desktop.QMessageBox.warning') as warning:
                w = Window(auto_discover=False)
                w.settings = replace(fixture.settings, port=listener.getsockname()[1])
                w.connecting_auto = True
                w.auto_enabled = True
                w.auto_candidates = [dict(host='198.51.100.2',port=52022,name='fallback')]
                w.show()
                w.connect_phone()
                self.wait_for(accepted.is_set)
                self.assertTrue(w.busy)
                started = time.monotonic()
                w.cancel_button.click()
                self.assertLess(time.monotonic()-started, .2, 'Cancel must not block the GUI')
                self.wait_for(lambda:not w.busy and not w.pool.activeThreadCount(),1)
                self.assertLess(time.monotonic()-started,1)
                self.assertFalse(w.connected)
                self.assertFalse(w.connecting_auto)
                self.assertFalse(w.auto_enabled)
                self.assertFalse(w.auto_candidates)
                self.assertTrue(w.cancel_button.isHidden())
                self.assertIn('已取消连接',w.status.text())
                w.auto_tick()
                QTest.qWait(40)
                self.assertFalse(w.maintenance_running)
                warning.assert_not_called()
                # An unrelated subsequent connection must get a fresh cancellation scope.
                w.settings, w.password = fixture.settings, 'test-password'
                w.start_thumbnails = lambda *args:None
                w.connect_phone()
                self.wait_for(lambda:w.connected and not w.busy,5)
                self.assertEqual(w.category,'recent')
                warning.assert_not_called()
        finally:
            release.set()
            listener.close()
            server.join(3)
            if w:
                w.busy=False
                w.close()
                self.app.processEvents()
            fixture.tearDown()

    def test_stalled_sftp_download_cancels_and_removes_local_partial_without_prefetch_leak(self):
        fixture = test_transfer.TransferTests('test_thumbnail_and_names')
        fixture.setUp()
        release, entered = threading.Event(), threading.Event()
        cancel = CancelEvent()
        errors=[]
        receiver=fixture.base/'downloads'
        source=fixture.remote/'large.bin'
        source.write_bytes(b'x'*(6*1024*1024))
        drained=threading.Event()
        original=paramiko.SFTPHandle.read
        def read(handle, offset, length):
            entered.set()
            release.wait(6)
            if cancel.is_set():
                handle.close()
                drained.set()
                raise EOFError()
            return original(handle,offset,length)
        def download():
            try:
                with fixture.phone(cancel) as phone:
                    _, entries=phone.list_dir('/')
                    phone.download(entries,receiver)
            except Exception as error:
                errors.append(error)
        worker=threading.Thread(target=download)
        with patch.object(paramiko.SFTPHandle,'read',read):
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                started=time.monotonic()
                cancel.set()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertLess(time.monotonic()-started,1)
                self.assertIsInstance(errors[0],Cancelled)
                self.assertFalse(list(receiver.iterdir()))
                self.assertEqual(source.stat().st_size,6*1024*1024)
                deadline=time.monotonic()+1
                while any('cancelled_prefetch' in t.name for t in threading.enumerate()) and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertFalse([t for t in threading.enumerate() if 'cancelled_prefetch' in t.name])
            finally:
                release.set()
                cancel.set()
                worker.join(3)
                self.assertTrue(drained.wait(2))
                fixture.tearDown()

    def test_stalled_sftp_upload_cancels_without_deleting_source_or_completed_files(self):
        fixture = test_transfer.TransferTests('test_thumbnail_and_names')
        fixture.setUp()
        release,entered=threading.Event(),threading.Event()
        cancel=CancelEvent()
        errors=[]
        source=fixture.base/'new.bin'
        source.write_bytes(b'y'*(12*1024*1024))
        (fixture.remote/'completed.txt').write_bytes(b'preserved')
        drained=threading.Event()
        original=paramiko.SFTPHandle.write
        def write(handle, offset, data):
            entered.set()
            release.wait(6)
            if cancel.is_set():
                handle.close()
                drained.set()
                raise EOFError()
            return original(handle,offset,data)
        def upload():
            try:
                with fixture.phone(cancel) as phone:
                    phone.upload([source],'/')
            except Exception as error:
                errors.append(error)
        worker=threading.Thread(target=upload)
        with patch.object(paramiko.SFTPHandle,'write',write):
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                started=time.monotonic()
                cancel.set()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertLess(time.monotonic()-started,1)
                self.assertIsInstance(errors[0],Cancelled)
                self.assertEqual(source.stat().st_size,12*1024*1024)
                self.assertEqual((fixture.remote/'completed.txt').read_bytes(),b'preserved')
                self.assertFalse((fixture.remote/'new.bin').exists())
            finally:
                release.set()
                cancel.set()
                worker.join(3)
                self.assertTrue(drained.wait(2))
                fixture.tearDown()

    def test_cancel_after_worker_completion_discards_queued_success(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ,LOCALAPPDATA=temp):
            w=Window(auto_discover=False)
            done=[]
            try:
                w.start_task(lambda signals:'late result',done.append,'working')
                self.assertTrue(w.pool.waitForDone(2000))
                w.cancel_current()
                self.wait_for(lambda:not w.busy)
                self.assertFalse(done)
                self.assertFalse(w.connected)
                self.assertIn('取消',w.status.text())
            finally:
                w.close()
                self.app.processEvents()


if __name__=='__main__':
    unittest.main()
