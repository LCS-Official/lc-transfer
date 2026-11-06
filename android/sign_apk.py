"""Sign the release APK with a persistent local key; never print the signing password."""
import json
import os
import secrets
import subprocess
import shutil
from pathlib import Path

root = Path(__file__).resolve().parent
workspace = root.parent
signing = root / 'signing'
signing.mkdir(exist_ok=True)
config = signing / 'signing.json'
if config.exists():
    password = json.loads(config.read_text(encoding='utf-8'))['password']
else:
    password = secrets.token_urlsafe(32)
    config.write_text(json.dumps({'password': password}), encoding='utf-8')
environment = os.environ.copy()
environment['JAVA_HOME'] = os.environ.get('JAVA_HOME') or str(Path(shutil.which('javac') or 'javac').resolve().parent.parent)
environment['LC_TRANSFER_SIGN_PASS'] = password
keystore = signing / 'lc-transfer.jks'
if not keystore.exists():
    subprocess.run([str(Path(environment['JAVA_HOME']) / 'bin/keytool.exe'), '-genkeypair',
                    '-keystore', str(keystore), '-storepass:env', 'LC_TRANSFER_SIGN_PASS',
                    '-keypass:env', 'LC_TRANSFER_SIGN_PASS', '-alias', 'lc-transfer',
                    '-keyalg', 'RSA', '-keysize', '3072', '-validity', '10000',
                    '-dname', 'CN=LC Transfer Development'], env=environment, check=True)
unsigned = root / 'upstream/wolpi-prim-ftpd-f0aa774/primitiveFTPd/build/outputs/apk/release/primitiveFTPd-release-unsigned.apk'
output = workspace / '发布'
output.mkdir(exist_ok=True)
aligned = root / 'lc-transfer-aligned.apk'
sdk = Path(os.environ.get('ANDROID_SDK_ROOT') or os.environ.get('ANDROID_HOME') or root / 'sdk')
tools = sdk / 'build-tools/36.0.0'
subprocess.run([str(tools / 'zipalign.exe'), '-f', '-p', '4', str(unsigned), str(aligned)], check=True)
apk = output / 'LC互传-安卓.apk'
subprocess.run([str(tools / 'apksigner.bat'), 'sign', '--ks', str(keystore), '--ks-key-alias',
                'lc-transfer', '--ks-pass', 'env:LC_TRANSFER_SIGN_PASS', '--key-pass',
                'env:LC_TRANSFER_SIGN_PASS', '--out', str(apk), str(aligned)], env=environment, check=True)
subprocess.run([str(tools / 'apksigner.bat'), 'verify', '--verbose', '--print-certs', str(apk)], env=environment, check=True)
aligned.unlink()
print('Signed APK:', apk)
