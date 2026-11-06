import gzip
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from PIL import Image

import test_transfer
from transfer_core import Entry, Phone


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_transfer.TransferTests('test_thumbnail_and_names')
        test_transfer.TransferTests.setUpClass()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_fast_catalog_uses_one_compressed_file_without_directory_scans(self):
        folder = self.fixture.remote / '.lc-transfer'
        folder.mkdir()
        payload = {'version': 1, 'files': [['DCIM/中文.jpg', 40_000_000, 1720000000],
                    ['Download/文档.pdf', 2000, 1720001000]], 'truncated': False}
        (folder / 'catalog.json.gz').write_bytes(gzip.compress(json.dumps(payload).encode()))
        with Phone(replace(self.fixture.settings, key_auth=False), 'test-password') as phone:
            with patch.object(phone, 'list_dir', side_effect=AssertionError('No remote directory walk')):
                entries, skipped, truncated = phone.scan()
                self.assertEqual([entry.path for entry in entries], ['/DCIM/中文.jpg', '/Download/文档.pdf'])
                self.assertFalse(skipped or truncated)
                self.assertEqual(phone.scan(limit=1)[2], True)
        self.assertFalse(any(kind == 'list' for kind, path in self.fixture.requests))

    def test_invalid_catalog_path_is_rejected(self):
        folder = self.fixture.remote / '.lc-transfer'
        folder.mkdir()
        with self.fixture.phone() as phone:
            for row in [['../escape.jpg', 1, 1], ['/absolute.jpg', 1, 1], ['ok.jpg', -1, 1], ['ok.jpg', 1, 'invalid']]:
                data = {'version': 1, 'files': [row]}
                (folder / 'catalog.json.gz').write_bytes(gzip.compress(json.dumps(data).encode()))
                with self.assertRaises(ValueError):
                    phone.scan()

    def test_preview_never_downloads_original_and_missing_preview_stays_an_icon(self):
        folder = self.fixture.remote / '.lc-transfer/previews/DCIM'
        folder.mkdir(parents=True)
        Image.new('RGB', (320, 240), '#abc123').save(folder / 'large.jpg.jpg', 'JPEG', quality=55)
        large = Entry('/DCIM/large.jpg', 'large.jpg', 80_000_000, 1700000000)
        missing = Entry('/DCIM/missing.jpg', 'missing.jpg', 9_000_000, 1700000000)
        with self.fixture.phone() as phone:
            result = phone.thumbnail(large)
            self.assertIsNotNone(result)
            self.assertEqual(phone.thumbnail(large), result)
            self.assertIsNone(phone.thumbnail(missing))
            with self.assertRaises(ValueError):
                phone.thumbnail(Entry('/../escape.jpg', 'escape.jpg', 1, 1))
        self.assertNotIn(('open', large.path), self.fixture.requests)
        self.assertNotIn(('open', missing.path), self.fixture.requests)
        self.assertEqual(self.fixture.requests.count(('open', '/.lc-transfer/previews/DCIM/large.jpg.jpg')), 1)

    def test_oversized_preview_is_rejected_without_loading_original(self):
        folder = self.fixture.remote / '.lc-transfer/previews'
        folder.mkdir(parents=True)
        (folder / 'large.jpg.jpg').write_bytes(b'x' * (96 * 1024 + 1))
        with self.fixture.phone() as phone:
            self.assertIsNone(phone.thumbnail(Entry('/large.jpg', 'large.jpg', 1, 1)))
        self.assertEqual([path for kind, path in self.fixture.requests if kind == 'open'],
                         ['/.lc-transfer/previews/large.jpg.jpg'])

    def test_video_preview_is_small_and_cache_survives_address_change(self):
        folder = self.fixture.remote / '.lc-transfer/previews/DCIM'
        folder.mkdir(parents=True)
        Image.new('RGB', (320, 180), '#abcdef').save(folder / 'clip.mp4.jpg', 'JPEG', quality=55)
        video = Entry('/DCIM/clip.mp4', 'clip.mp4', 2_000_000_000, 1700000000)
        with self.fixture.phone() as phone:
            preview = phone.thumbnail(video)
            self.assertIsNotNone(preview)
            phone.settings = replace(phone.settings, host='198.51.100.2')
            self.assertEqual(phone.thumbnail(video), preview)
        self.assertEqual([path for kind, path in self.fixture.requests if kind == 'open'],
                         ['/.lc-transfer/previews/DCIM/clip.mp4.jpg'])
