"""One-time setup for the owner's private journal. Never put a token in git or print credentials."""
import argparse
import re
import subprocess

from backend.cloud_client import CloudPaper, MARKER, github_session
from backend.cloud_job import run_market, seed, write
from backend.settings import ROOT

OWNER = 'Oliver0190'
APP = OWNER + '/stock-research-web'
RUNTIME = OWNER + '/stock-paper-runtime'
API = 'https://api.github.com'


def checked(r, *codes):
    if r.status_code not in codes:
        raise RuntimeError(f'GitHub HTTP {r.status_code} on {r.request.method} {r.request.path_url}')
    return r.json() if r.content else {}


def private(s):
    repo = checked(s.get(f'{API}/repos/{RUNTIME}', timeout=10), 200)
    if not repo['private'] or repo['full_name'] != RUNTIME or not repo['permissions']['admin']:
        raise ValueError('Expected owner-administered private runtime repo')


def install(s):
    sha = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    remote = checked(s.get(f'{API}/repos/{APP}/git/ref/heads/main',timeout=12),200)['object']['sha']
    if sha != remote or not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('Publish the verified app commit to the website repo before installation')
    target = ROOT/'data'/'paper-runtime-setup'
    if target.exists():
        raise ValueError('Setup copy already exists; refusing to overwrite journals')
    user = checked(s.get(f'{API}/user',timeout=10),200)
    if user['login'] != OWNER:
        raise ValueError('Wrong GitHub account')
    exists = s.get(f'{API}/repos/{RUNTIME}',timeout=10)
    if exists.status_code != 404:
        raise ValueError('Runtime repository already exists or could not be checked; refusing to overwrite it')
    for market in ('A','HK'):
        seed(target/'state', market)
        run_market(target/'state', market, 'verify')
    (target/'.github/workflows').mkdir(parents=True)
    workflow = (ROOT/'ops/paper-workflow.yml').read_text(encoding='utf-8').replace('__APPLICATION_SHA__',sha)
    (target/'.github/workflows/paper.yml').write_text(workflow,encoding='utf-8')
    (target/'README.md').write_text(f'# 模拟交易 · 私有账本\n\n[A 股账户](state/A) · [港股账户](state/HK) · [运行与暂停](https://github.com/{RUNTIME}/actions/workflows/paper.yml)\n\n'
        f'应用代码固定为 [{sha[:7]}](https://github.com/{APP}/commit/{sha})。此仓库存储虚拟账本，请保持私有。\n',encoding='utf-8')
    checked(s.post(f'{API}/user/repos',json={'name':RUNTIME.split('/')[1], 'private':True,'auto_init':False,
        'description':'Private paper-trading journal for stock-research-web','has_issues':False,'has_projects':False,'has_wiki':False}, timeout=15),201)
    private(s)
    for args in (['init','-b','main'],['add','--','.github/workflows/paper.yml','state','README.md'],
                 ['commit','-m','Initialize private paper journal'],['remote','add','origin',f'https://github.com/{RUNTIME}.git'],
                 ['push','-u','origin','main']):
        subprocess.run(['git',*args], cwd=target, check=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['install','link','verify','start','pause','run','status']);p.add_argument('--market',choices=['both','A','HK'],default='both');args=p.parse_args()
    with github_session() as s:
        if args.action=='install':
            return install(s)
        private(s)
        if args.action=='link':
            for m in ('A','HK'):
                obj=CloudPaper(RUNTIME,m).snapshot()
                if obj['cloud_stale'] or obj['market']!=m:
                    raise ValueError('Cannot link to an unverified journal')
            write(MARKER,{'repository':RUNTIME}); print('Cloud linked. Restart the local website.');return
        if args.action=='status':
            runs=checked(s.get(f'{API}/repos/{RUNTIME}/actions/workflows/paper.yml/runs',params={'per_page':3},timeout=12),200)
            for r in runs['workflow_runs']: print(r['id'],r['status'],r['conclusion'],r['html_url'])
            return
        checked(s.post(f'{API}/repos/{RUNTIME}/actions/workflows/paper.yml/dispatches',json={'ref':'main','inputs':{'operation':args.action,'market':args.market}},timeout=12),200,204)
        print('Submitted',args.action,args.market)


if __name__=='__main__':main()
