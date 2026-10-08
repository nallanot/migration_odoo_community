"""Agent Docker qui reçoit les travaux du plugin privé, sans accès entrant public."""
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.request

from migration import Engine, MAX_UPLOAD_BYTES


class RemoteWorker:
    def __init__(self, root, site_url, pairing_code, service_token, interval=15):
        if not site_url.startswith('https://') or '/' in site_url[8:].rstrip('/'):
            raise ValueError('MIGRATION_PLUGIN_SITE_URL doit être une origine HTTPS.')
        if not re.fullmatch(r'[a-f0-9]{64}', pairing_code):
            raise ValueError('Code d’appairage invalide.')
        if not service_token:
            raise ValueError('Jeton de service privé du Site manquant.')
        self.root = Path(root)
        self.site = site_url.rstrip('/')
        self.token = pairing_code
        self.service_token = service_token
        self.interval = interval

    def request(self, path, data=None, method=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.site + path, data=body, method=method or ('POST' if body else 'GET'),
            headers={'X-Migration-Worker-Token':self.token,
                     'OAI-Sites-Authorization':'Bearer ' + self.service_token,
                     'Content-Type':'application/json'})
        with urllib.request.urlopen(req, timeout=45) as response:
            return json.loads(response.read())

    def set_status(self, job_id, status, engine=None, blocker=None):
        stage = engine.state.get('automation', {}).get('stage', '') if engine else ''
        logs = engine.logs[-12:] if engine else []
        self.request(f'/worker/jobs/{job_id}/status', {
            'status':status, 'stage':stage or status, 'blocker':blocker,
            'log_tail':'\n'.join(logs)[-12000:],
            'metrics':engine.state.get('metrics') if engine else None,
        })

    def download(self, job, engine):
        path = engine.root / 'incoming'
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = path / (job['id'] + '.upload')
        count = 0
        with target.open('wb') as output:
            for index in range(job['parts']):
                req = urllib.request.Request(self.site + f"/worker/jobs/{job['id']}/chunk/{index}",
                     headers={'X-Migration-Worker-Token':self.token,
                              'OAI-Sites-Authorization':'Bearer ' + self.service_token})
                with urllib.request.urlopen(req, timeout=90) as response:
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        output.write(block)
                        count += len(block)
                        if count > MAX_UPLOAD_BYTES:
                            raise ValueError('Sauvegarde trop volumineuse.')
        os.chmod(target, 0o600)
        if count != job['size_bytes']:
            raise ValueError('Taille de sauvegarde incorrecte après téléchargement.')
        return target

    def apply_patches(self, job_id, engine):
        files = self.request(f'/worker/jobs/{job_id}/patches')['files']
        for entry in files:
            rel = str(entry['path'])
            if not re.fullmatch(r'(1[3-9])/([a-z0-9_][a-z0-9_.-]*/)+[a-z0-9_.-]+\.(py|xml|csv|json)', rel):
                raise ValueError('Chemin de module refusé.')
            target = engine.root / 'extra' / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(entry['content'])
            os.chmod(target, 0o600)
        return len(files)

    def process(self, job):
        job_id = job['id']
        engine = Engine(self.root / 'jobs' / job_id)
        stop = threading.Event()
        def heartbeat():
            while not stop.wait(25):
                try:
                    self.set_status(job_id, 'running', engine)
                except Exception:
                    pass
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            if not engine.state.get('backup'):
                incoming = self.download(job, engine)
                engine.import_backup({'file':str(incoming)})
            self.apply_patches(job_id, engine)
            engine.autopilot({'ack_auto':True})
            if engine.state['version'] == 19:
                self.set_status(job_id, 'completed', engine)
            else:
                self.set_status(job_id, 'blocked', engine, 'Étape incomplète; consulter le journal.')
        except Exception as exc:
            stage=engine.state.get('automation', {}).get('blocker') or str(exc)
            self.set_status(job_id, 'blocked' if engine.state.get('backup') else 'failed', engine, stage)
        finally:
            stop.set()
            thread.join(timeout=2)

    def run_forever(self):
        while True:
            try:
                job = self.request('/worker/next').get('job')
                if job:
                    self.process(job)
            except Exception as exc:
                # Le serveur local reste disponible même si le plugin est momentanément injoignable.
                print('Agent distant : ' + str(exc)[:300], flush=True)
            time.sleep(self.interval)


def start_if_configured(root):
    site = os.getenv('MIGRATION_PLUGIN_SITE_URL', '').strip()
    token = os.getenv('MIGRATION_PLUGIN_TOKEN', '').strip()
    service_token = os.getenv('MIGRATION_PLUGIN_SERVICE_TOKEN', '').strip()
    if not site and not token and not service_token:
        return None
    worker = RemoteWorker(root, site, token, service_token)
    thread = threading.Thread(target=worker.run_forever, name='migration-plugin-worker', daemon=True)
    thread.start()
    return thread
