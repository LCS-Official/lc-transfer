"""Collect installed dependency licenses into the portable desktop release."""
import importlib.metadata
import shutil
import sys
from pathlib import Path

target = Path('发布/LC互传/licenses')
target.mkdir(parents=True, exist_ok=True)
packages = ['paramiko', 'PySide6', 'PySide6-Essentials', 'shiboken6', 'Pillow', 'cryptography',
            'bcrypt', 'PyNaCl', 'cffi', 'pycparser', 'invoke', 'packaging']
for package in packages:
    try:
        dist = importlib.metadata.distribution(package)
    except importlib.metadata.PackageNotFoundError:
        continue
    for entry in dist.files or []:
        lower = str(entry).lower()
        if any(term in Path(lower).name for term in ['license', 'licence', 'copying', 'notice']):
            source = Path(dist.locate_file(entry))
            if source.is_file():
                dest = target / package / str(entry).replace('/', '__').replace('\\', '__')
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest)
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if python_license.exists():
    shutil.copyfile(python_license, target / 'Python-LICENSE.txt')
print('Dependency licenses copied to', target)
qt_licenses = Path('third_party_licenses')
if qt_licenses.exists():
    shutil.copytree(qt_licenses, target / 'qt-source-licenses', dirs_exist_ok=True)
