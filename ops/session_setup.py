"""Configure one private journal deploy key and repository-scoped Actions secrets."""
import argparse
import base64
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from backend.cloud_client import github_session
from backend.settings import ROOT
from ops.paper_admin import API, APP, RUNTIME, checked, private


def store_secret(session, name, value):
    from nacl.public import PublicKey, SealedBox
    key = checked(session.get(f'{API}/repos/{APP}/actions/secrets/public-key', timeout=12), 200)
    ciphertext = SealedBox(PublicKey(base64.b64decode(key['key']))).encrypt(value)
    checked(session.put(f'{API}/repos/{APP}/actions/secrets/{name}', json={
        'key_id': key['key_id'], 'encrypted_value': base64.b64encode(ciphertext).decode()
    }, timeout=12), 201, 204)


def journal_key(session):
    keys = checked(session.get(f'{API}/repos/{RUNTIME}/keys', timeout=12), 200)
    if any(key['title'] == 'stock-research-web paper sessions' for key in keys):
        raise ValueError('Deployment key already exists')
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'session_key'
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-C','paper-sessions','-N','','-f',str(path)], check=True)
        secret_url = f'{API}/repos/{APP}/actions/secrets/PAPER_JOURNAL_DEPLOY_KEY'
        if session.get(secret_url, timeout=12).status_code != 404:
            raise ValueError('Refusing to replace an existing journal credential')
        store_secret(session, 'PAPER_JOURNAL_DEPLOY_KEY', path.read_bytes())
        checked(session.post(f'{API}/repos/{RUNTIME}/keys', json={
            'title':'stock-research-web paper sessions', 'key':path.with_suffix('.pub').read_text().strip(),
            'read_only':False}, timeout=12), 201)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['journal-key','model-secret','activate','check','status'])
    args = parser.parse_args()
    with github_session() as session:
        private(session)
        app = checked(session.get(f'{API}/repos/{APP}', timeout=12), 200)
        if app['private'] or not app['permissions']['admin'] or app['full_name'] != APP:
            raise ValueError('Expected the owner-administered public website repo')
        if args.action in {'model-secret','journal-key'}:
            sys.path.insert(0, str(ROOT / 'data' / 'paper-setup-packages'))
        if args.action == 'journal-key':
            journal_key(session)
        elif args.action == 'model-secret':
            status = session.get(f'{API}/repos/{APP}/actions/secrets/DEEPSEEK_API_KEY', timeout=12)
            if status.status_code == 404:
                store_secret(session, 'DEEPSEEK_API_KEY', os.environ['DEEPSEEK_API_KEY'].encode())
            else:
                checked(status, 200)
        elif args.action == 'activate':
            for name in ('PAPER_JOURNAL_DEPLOY_KEY','DEEPSEEK_API_KEY'):
                checked(session.get(f'{API}/repos/{APP}/actions/secrets/{name}',timeout=12),200)
            url = f'{API}/repos/{APP}/actions/variables'
            value = {'name':'PAPER_SESSIONS_ENABLED','value':'true'}
            if session.get(url+'/PAPER_SESSIONS_ENABLED',timeout=12).status_code == 404:
                checked(session.post(url,json=value,timeout=12),201)
            else:
                checked(session.patch(url+'/PAPER_SESSIONS_ENABLED',json=value,timeout=12),204)
        elif args.action == 'check':
            checked(session.post(f'{API}/repos/{APP}/actions/workflows/paper-sessions.yml/dispatches',
                    json={'ref':'main','inputs':{'window':'check'}},timeout=12),200,204)
        else:
            result = checked(session.get(f'{API}/repos/{APP}/actions/workflows/paper-sessions.yml/runs',
                params={'per_page':3}, timeout=12),200)
            for r in result['workflow_runs']:
                print(r['id'],r['status'],r['conclusion'],r['html_url'])
        print(args.action, 'OK')


if __name__ == '__main__':
    main()
