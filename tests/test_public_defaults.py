import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from discovery import discover, targets
from desktop import ConnectDialog
from transfer_core import Settings
from PySide6.QtWidgets import QApplication
import paramiko


class PublicDefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        cls.app=QApplication.instance() or QApplication([])

    def test_clean_install_has_no_peer_or_fixed_remote_probe(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,LOCALAPPDATA=folder):
            settings=Settings.load()
            self.assertEqual(settings.host,'')
            self.assertEqual(settings.fallback_host,'')
            self.assertEqual(settings.port,52022)
            self.assertEqual(targets('',[]),[])
            with patch('discovery.lan_interfaces',return_value=[]), patch('discovery.socket.socket') as socket:
                self.assertEqual(discover('',paramiko.ECDSAKey.generate(),timeout=0),[])
                socket.assert_not_called()

    def test_optional_fallback_roundtrips_without_losing_paired_identity(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,LOCALAPPDATA=folder):
            settings=Settings(key_auth=True,fingerprint='SHA256:'+'A'*43)
            dialog=ConnectDialog(settings,'',None)
            dialog.fallback.setText('198.51.100.44')
            dialog.fallback_port.setValue(52100)
            configured=dialog.result_settings()
            configured.save()
            loaded=Settings.load()
            self.assertEqual(loaded.fallback_host,'198.51.100.44')
            self.assertEqual(loaded.fallback_port,52100)
            self.assertEqual(loaded.fingerprint,settings.fingerprint)
            self.assertTrue(loaded.key_auth)
            self.assertEqual(targets('',[],fallback_host=loaded.fallback_host),['198.51.100.44'])
            dialog.close()

    def test_source_guard_rejects_new_credentials_and_runtime_state(self):
        from tools.check_public_source import problems
        self.assertIn('private/generated file',problems(Path('android/signing/device.jks'),b'key data',{}))
        self.assertIn('private key',problems(Path('oops.txt'),b'-----BEGIN '+b'PRIVATE KEY-----',{}))
        self.assertIn('GitHub token',problems(Path('oops.txt'),b'ghp_'+b'a'*40,{}))
        self.assertFalse(problems(Path('desktop.py'),b'print("hello")',{}))
