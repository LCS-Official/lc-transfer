"""Fetch the official Android command-line SDK into this workspace."""
import hashlib
import pathlib
import zipfile
import xml.etree.ElementTree as ET

import requests

root = pathlib.Path(__file__).resolve().parent
sdk = root / 'sdk'
sdk.mkdir(exist_ok=True)
url_base = 'https://dl.google.com/android/repository/'
r = requests.get(url_base + 'repository2-1.xml', timeout=60)
r.raise_for_status()
tree = ET.fromstring(r.content)
package = next(p for p in tree if p.tag.endswith('remotePackage') and p.attrib.get('path') == 'cmdline-tools;19.0')
archive = next(a for a in package.find('archives') if a.findtext('host-os') == 'windows')
complete = archive.find('complete')
url = url_base + complete.findtext('url')
dest = root / 'commandlinetools.zip'
print('Downloading', url, flush=True)
digest = hashlib.sha1()
with requests.get(url, timeout=180, stream=True) as response:
    response.raise_for_status()
    with dest.open('wb') as output:
        for block in response.iter_content(1024 * 1024):
            output.write(block)
            digest.update(block)
if digest.hexdigest() != complete.findtext('checksum'):
    raise RuntimeError('Android SDK checksum mismatch')
target = sdk / 'cmdline-tools' / '19.0'
with zipfile.ZipFile(dest) as archive:
    for entry in archive.infolist():
        relative = pathlib.PurePosixPath(entry.filename)
        if not relative.parts or relative.parts[0] != 'cmdline-tools' or '..' in relative.parts:
            raise RuntimeError('Unexpected SDK archive path')
        local = target.joinpath(*relative.parts[1:])
        if entry.is_dir():
            local.mkdir(parents=True, exist_ok=True)
        else:
            local.parent.mkdir(parents=True, exist_ok=True)
            with local.open('wb') as output:
                output.write(archive.read(entry))
dest.unlink()
print('SDK command-line tools ready:', target, flush=True)
