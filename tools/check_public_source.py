"""Guard tracked public source against common accidental credential disclosures."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.git','.venv','.build-env','.test-state','__pycache__','build','.gradle','发布','sdk','diagnostics','signing'}
RULES = {
    'private key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----'),
    'GitHub token': re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})'),
    'AWS access key': re.compile(rb'AKIA[0-9A-Z]{16}'),
    'personal Windows path': re.compile(rb'[A-Za-z]:[\\/]+Users[\\/]+[^\s\\/]+'),
}
FORBIDDEN_SUFFIXES = {'.jks','.keystore','.pem','.p12','.pfx','.log','.lnk','.apk','.aab'}
FORBIDDEN_NAMES = {'signing.json','settings.json','transfers.json','.env','computer-key.pem','local.properties'}


def problems(relative, payload, fixture_hashes):
    name = relative.as_posix()
    if name in fixture_hashes and hashlib.sha256(payload).hexdigest() == fixture_hashes[name]:
        return []
    issues=[]
    if relative.suffix.lower() in FORBIDDEN_SUFFIXES or relative.name in FORBIDDEN_NAMES or any(p in {'signing','diagnostics','.lc-transfer'} for p in relative.parts):
        issues.append('private/generated file')
    if b'\x00' not in payload:
        if re.search(rb'[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]', payload):
            issues.append('hidden control character in text')
        for label, pattern in RULES.items():
            if pattern.search(payload): issues.append(label)
    return issues


def main():
    fixture_hashes=json.loads((ROOT/'tools/upstream_test_keys.json').read_text(encoding='utf-8'))
    git=subprocess.run(['git','ls-files','-z'],cwd=ROOT,capture_output=True)
    if git.returncode and (ROOT/'.git').exists():
        raise SystemExit('Cannot inspect Git index; run in the repository owner context.')
    if git.returncode==0 and git.stdout:
        paths=[ROOT/name.decode('utf-8') for name in git.stdout.split(b'\0') if name]
    else:
        paths=[p for p in ROOT.rglob('*') if p.is_file() and not any(part in EXCLUDED for part in p.relative_to(ROOT).parts)]
    failures=[]
    for path in paths:
        relative=path.relative_to(ROOT)
        for issue in problems(relative,path.read_bytes(),fixture_hashes):
            failures.append(f'{relative.as_posix()}: {issue}')
    if failures: raise SystemExit('\n'.join(failures))
    print(f'Public source checks passed for {len(paths)} files; only verified upstream test-key fixtures allowed.')


if __name__=='__main__':
    main()
