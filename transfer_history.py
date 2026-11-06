"""Persist transfer jobs and mirror their status to the Android client's native tab."""
import copy
import json
import os
import posixpath
import threading
import time
import uuid
import paramiko

from transfer_core import data_dir


class History:
    def __init__(self):
        self.lock = threading.RLock()
        self.path = data_dir() / 'transfers.json'
        self.warning = ''
        try:
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(self.data.get('jobs'), list):
                raise ValueError('invalid history')
        except (OSError, ValueError, AttributeError):
            self.data = {'version': 1, 'updated': 0, 'jobs': []}
        for job in self.data['jobs']:
            if job.get('state') == 'running':
                job['state'] = 'interrupted'
                job['error'] = '上次运行意外中断，部分文件可能未完成。'
                for item in job.get('files', []):
                    if item.get('state') == 'running':
                        item['state'] = 'interrupted'
                    elif item.get('state') == 'packed':
                        item['state'] = 'discarded'
        self.last_saved = 0

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def save(self, force=False):
        with self.lock:
            now = time.time()
            if not force and now - self.last_saved < .5:
                return
            self.data['updated'] = now
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
            os.replace(temp, self.path)
            self.last_saved = now

    def bind_device(self, host, fingerprint):
        if not fingerprint:
            return
        with self.lock:
            changed = False
            for job in self.data['jobs']:
                if job.get('host') == host and not job.get('fingerprint'):
                    job['fingerprint'] = fingerprint
                    changed = True
            if changed:
                self.save(True)

    def start(self, direction, names, target, host, fingerprint='', share_id=''):
        with self.lock:
            job = {'id': uuid.uuid4().hex, 'direction': direction, 'started': time.time(),
                   'state': 'running', 'selected': names, 'target': target, 'host': host,
                   'files': [], 'error': '', 'archive': '', 'fingerprint': fingerprint}
            if share_id:
                job['share_id'] = share_id
            self.data['jobs'].insert(0, job)
            self.data['jobs'] = self.data['jobs'][:100]
            self.save(True)
            return job['id']

    def event(self, job_id, event):
        with self.lock:
            job = next(j for j in self.data['jobs'] if j['id'] == job_id)
            item = next((f for f in job['files'] if f['path'] == event['path']), None)
            if item is None:
                item = {}
                job['files'].append(item)
            item.update(event)
            self.save()

    def finish(self, job_id, state, error='', archive=''):
        with self.lock:
            job = next(j for j in self.data['jobs'] if j['id'] == job_id)
            job.update(state=state, error=error, archive=archive, finished=time.time())
            for item in job['files']:
                if state == 'done' and item['state'] == 'packed':
                    item['state'] = 'done'
                elif state != 'done' and item['state'] == 'packed':
                    item['state'] = 'discarded'
                elif item['state'] == 'running':
                    item['state'] = state
            self.save(True)

    def mirror(self, phone):
        """No additional listening port: the same authenticated SFTP channel writes status."""
        folder = posixpath.join(phone.root, '.lc-transfer')
        try:
            try:
                phone.sftp.mkdir(folder)
            except OSError:
                phone.sftp.stat(folder)
            folder = phone.sftp.normalize(folder)
            root = phone.root.rstrip('/')
            if root and not folder.startswith(root + '/'):
                raise ValueError('手机传输记录目录超出了共享存储。')
            data = self.snapshot()
            data['jobs'] = [job for job in data['jobs'] if
                (job['fingerprint'] == phone.settings.fingerprint if job.get('fingerprint')
                 else job.get('host') == phone.settings.host)]
            data['updated'] = time.time()
            payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
            with phone.sftp.open(posixpath.join(folder, 'history.json'), 'wb') as output:
                output.write(payload)
            self.warning = ''
        except (OSError, ValueError, EOFError, paramiko.SSHException) as ex:
            self.warning = '手机端传输记录同步失败：' + str(ex)
