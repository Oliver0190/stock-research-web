"""Authenticated, local-only reader and dispatcher for the private paper runner."""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

import requests

from backend.settings import ROOT

MARKER = ROOT / 'data' / 'paper-cloud.json'


class CloudError(RuntimeError):
    pass


def github_session():
    token = os.environ.get('STOCK_AGENT_GITHUB_TOKEN')
    if not token:
        try:
            result = subprocess.run(['git', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n',
                capture_output=True, text=True, timeout=6, env={**os.environ,'GCM_INTERACTIVE':'Never','GIT_TERMINAL_PROMPT':'0'})
            creds = dict(x.split('=',1) for x in result.stdout.splitlines() if '=' in x)
            token = creds.get('password') if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            token = None
    if not token:
        raise CloudError('无法访问私有模拟仓库，请检查本机 GitHub 登录。')
    s = requests.Session()
    s.headers.update({'Authorization': 'Bearer ' + token, 'Accept':'application/vnd.github+json', 'X-GitHub-Api-Version':'2022-11-28'})
    return s


class CloudPaper:
    def __init__(self, repo, market, cache_dir=None):
        if not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_-]+',repo) or market not in {'HK','A'}:
            raise ValueError('Invalid cloud account configuration')
        self.repo, self.market = repo, market
        self.url = f'https://github.com/{repo}/actions/workflows/paper.yml'
        self.dir = (cache_dir or ROOT / 'data' / 'cloud-view') / repo.replace('/','_') / market
        self.lock = threading.Lock()
        self.updated = 0

    def call(self, method, path, *, raw=False, **kwargs):
        try:
            with github_session() as s:
                r = s.request(method, f'https://api.github.com/repos/{self.repo}/{path}', timeout=(3, 8), allow_redirects=False,
                    headers={'Accept':'application/vnd.github.raw+json' if raw else 'application/vnd.github+json'}, **kwargs)
                if r.status_code not in {200,201,204}:
                    raise CloudError(f'GitHub 返回 {r.status_code}，请检查私有仓库访问或任务权限。')
                return r.json() if r.content else None
        except (requests.RequestException, ValueError) as exc:
            raise CloudError('暂时无法连接 GitHub，请稍后重试。') from exc

    def snapshot(self):
        path = self.dir / 'view.json'
        stale = False
        with self.lock:
            if time.monotonic() - self.updated > 45 or not path.exists():
                try:
                    obj = self.call('GET', f'contents/state/{self.market}/view.json', raw=True)
                    if obj.get('market') != self.market or 'cloud_updated_at' not in obj:
                        raise CloudError('云端账本尚未就绪。')
                    self.dir.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_suffix('.tmp')
                    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
                    tmp.replace(path)
                    self.updated = time.monotonic()
                except CloudError:
                    if not path.is_file():
                        raise
                    stale = True
            obj = json.loads(path.read_text(encoding='utf-8'))
        return {**obj, 'cloud': True, 'cloud_stale': stale, 'cloud_actions': self.url}

    def decision(self, identifier):
        if not re.fullmatch(r'[a-f0-9]{32}', identifier):
            return None
        return self.call('GET', f'contents/state/{self.market}/decisions/{identifier}.json', raw=True)

    def dispatch(self, operation, items=None):
        if operation not in {'run','start','pause','sync'}:
            raise ValueError('Invalid operation')
        inputs = {'operation':operation, 'market':self.market}
        if items is not None:
            from backend.cloud_job import watch_items
            inputs['watchlist'] = json.dumps(watch_items(items, self.market), ensure_ascii=False)
        self.call('POST', 'actions/workflows/paper.yml/dispatches', json={'ref':'main','inputs':inputs})
        self.updated = 0
        return {'started':True,'queued':True,'cloud':True}


def configured_cloud():
    if not MARKER.exists():
        return None
    # Fail closed on malformed configuration: never start a local writer as fallback.
    return json.loads(MARKER.read_text(encoding='utf-8'))['repository']
