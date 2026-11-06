"""Create portable Windows and corresponding source archives, excluding local secrets/tools."""
import hashlib
import os
import shutil
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent
output = root / '发布'
client = output / 'LC互传'
for name in ['README.md', '手机设置.html', 'THIRD_PARTY_NOTICES.md','LICENSE','NOTICE']:
    shutil.copyfile(root / name, output / name)
    shutil.copyfile(root / name, client / name)
shutil.copyfile(root / 'android/LICENSE-PrimitiveFTPd.txt', output / 'LICENSE-PrimitiveFTPd.txt')
shutil.copyfile(root / 'android/MODIFICATIONS.md', output / 'Android改造说明.md')
for path in client.glob('*.smoke.log'):
    path.unlink()

windows = output / 'LC互传-Windows便携版.zip'
with zipfile.ZipFile(windows, 'w', zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
    for path in client.rglob('*'):
        if path.is_file():
            archive.write(path, path.relative_to(output))

source_zip = output / 'LC互传-完整源码.zip'
with zipfile.ZipFile(source_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for name in ['desktop.py', 'image_preview.py', 'transfer_core.py', 'transfer_history.py', 'discovery.py', 'requirements.txt',
                 'README.md', '手机设置.html', 'THIRD_PARTY_NOTICES.md', 'build.ps1',
                 'collect_licenses.py', 'package_release.py', 'prepare_launchers.ps1', '启动电脑端.cmd', 'LICENSE', 'NOTICE', '.gitignore', 'SECURITY.md']:
        archive.write(root / name, name)
    for base in [root / 'tools', root / '.github', root / 'tests', root / 'third_party_licenses', root / 'android/upstream/wolpi-prim-ftpd-f0aa774']:
        if not base.exists():
            continue
        for folder, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in {'build', '.gradle', '__pycache__', '.git'} and not (Path(folder) == base and d == 'android')]
            for name in files:
                if name == 'local.properties' or name.endswith(('.apk', '.jks', '.part', '.lck')):
                    continue
                path = Path(folder) / name
                archive.write(path, path.relative_to(root))
    for name in ['build.ps1', 'sign_apk.py', 'prepare_sdk.py', 'MODIFICATIONS.md', 'LICENSE-PrimitiveFTPd.txt']:
        archive.write(root / 'android' / name, 'android/' + name)

artifacts = [windows, source_zip, output / 'LC互传-安卓.apk', client / 'LC互传.exe']
lines = [hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + str(path.relative_to(output)) for path in artifacts]
(output / 'SHA256.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
for path in artifacts:
    print(path.name, round(path.stat().st_size / 1024 / 1024, 2), 'MB')
assert all(path.is_file() and path.stat().st_size > 0 for path in artifacts)
with zipfile.ZipFile(source_zip) as check:
    assert not any('/signing/' in n or n.endswith('local.properties') for n in check.namelist())
