"""Interoperate with the actual Java classes packaged into the Android APK."""
import json
import os
import subprocess
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import paramiko

from discovery import identity, key_fingerprint, pairing_code
from transfer_core import Phone, Settings

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'android/upstream/wolpi-prim-ftpd-f0aa774'
JDK = (Path(os.environ['JAVA_HOME']) / 'bin' if os.environ.get('JAVA_HOME')
       else Path(shutil.which('javac') or 'javac').resolve().parent)


class AndroidProtocolTests(unittest.TestCase):
    def test_actual_android_ssh_public_key_auth_and_sftp(self):
        classes = SOURCE / 'primitiveFTPd/build/intermediates/javac/release/compileReleaseJavaWithJavac/classes'
        if not classes.exists() or not (JDK / 'javac.exe').exists():
            self.skipTest('Build the Android release and install JDK 21 for the Java interoperability check')
        modules = Path(os.environ.get('GRADLE_USER_HOME', str(ROOT / 'android/.gradle'))) / 'caches/modules-2/files-2.1'
        jars = []
        for group, artifact in [('org.apache.mina', 'mina-core'), ('org.slf4j', 'slf4j-api'),
                                ('org.bouncycastle', 'bcprov-jdk18on'), ('org.bouncycastle', 'bcpkix-jdk18on'),
                                ('org.bouncycastle', 'bcutil-jdk18on')]:
            candidates = list((modules / group / artifact).rglob('*.jar'))
            self.assertTrue(candidates, artifact)
            jars.append(max(candidates, key=lambda path: path.stat().st_mtime))
        jars += [SOURCE / 'sshd-core-0.14.0/build/intermediates/aar_main_jar/release/syncReleaseLibJars/classes.jar',
                 SOURCE / 'pftpd-pojo-lib/build/libs/pftpd-pojo-lib.jar',
                 Path(os.environ.get('ANDROID_SDK_ROOT') or os.environ.get('ANDROID_HOME') or ROOT / 'android/sdk') / 'platforms/android-36/android.jar']
        self.assertTrue(all(path.exists() for path in jars), jars)
        (ROOT / '.test-state').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.test-state') as temp, patch.dict(os.environ, LOCALAPPDATA=temp):
            base = Path(temp)
            key = identity()
            (base / 'pc.pub').write_text(key.get_name() + ' ' + key.get_base64())
            root = base / 'phone'
            state = root / '.lc-transfer'
            receiver = root / 'Download/互传'
            state.mkdir(parents=True)
            receiver.mkdir(parents=True)
            (state / 'device.json').write_text(json.dumps({'receive_dir': '/Download/互传'}))
            classpath = os.pathsep.join(map(str, [classes, *jars]))
            compiled = subprocess.run([str(JDK / 'javac.exe'), '-encoding', 'UTF-8', '-cp', classpath,
                                       '-d', str(base), str(ROOT / 'tests/AndroidBridgeCheck.java')],
                                      capture_output=True, timeout=40)
            self.assertEqual(compiled.returncode, 0, compiled.stderr.decode(errors='replace'))
            with (base / 'java.log').open('w') as log:
                server = subprocess.Popen([str(JDK / 'java.exe'), '-cp', str(base) + os.pathsep + classpath,
                                           'AndroidBridgeCheck', str(base / 'pc.pub'), str(root), str(base / 'ready')],
                                          stdin=subprocess.PIPE, stdout=log, stderr=log,
                                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                try:
                    deadline = time.monotonic() + 20
                    while not (base / 'ready').exists() and time.monotonic() < deadline and server.poll() is None:
                        time.sleep(.05)
                    self.assertTrue((base / 'ready').exists(), (base / 'java.log').read_text())
                    port, fingerprint, code = (base / 'ready').read_text().splitlines()
                    self.assertEqual(code, pairing_code(fingerprint, key_fingerprint(key)))
                    settings = Settings('127.0.0.1', int(port), 'user', '/', str(base / 'downloads'), fingerprint, True)
                    source = base / 'from-pc.zip'
                    source.write_bytes(b'ZIP transfer contents')
                    with Phone(settings, '') as phone:
                        self.assertEqual(phone.device_config(), '/Download/互传')
                        phone.heartbeat()
                        self.assertEqual(phone.upload([source], phone.device_config()), 1)
                        self.assertEqual((receiver / source.name).read_bytes(), source.read_bytes())
                        entries = phone.list_dir('/Download/互传')[1]
                        count, archive = phone.download_zip(entries, base / 'downloads')
                        self.assertEqual(count, 1)
                    # A different private key must not authenticate with the paired phone.
                    with patch('discovery.identity', return_value=paramiko.ECDSAKey.generate()):
                        with self.assertRaises(paramiko.AuthenticationException):
                            with Phone(settings, ''):
                                pass
                except Exception:
                    log.flush()
                    print((base / 'java.log').read_text(errors='replace'))
                    raise
                finally:
                    try:
                        server.communicate(b'\n', timeout=10)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.communicate(timeout=5)
