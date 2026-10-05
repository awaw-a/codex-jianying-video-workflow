"""Ask actual local Codex skills/list; never create a conversation or invoke a model."""
from pathlib import Path
import argparse, json, os, queue, shutil, subprocess, threading, time

NAMES={'jianying-amv-workflow','video-media-review','video-workflow-learning'}
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cwd',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    script=Path(os.environ.get('APPDATA',Path.home()/'AppData'/'Roaming'))/'npm'/'node_modules'/'@openai'/'codex'/'bin'/'codex.js'
    node=shutil.which('node')
    if not node or not script.is_file():raise RuntimeError('Locate the actual Codex CLI installation before verification')
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    proc=subprocess.Popen([node,str(script),'app-server'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL,text=True,encoding='utf-8',creationflags=flags)
    messages=queue.Queue()
    def reader():
        for line in proc.stdout:
            try:messages.put(json.loads(line))
            except ValueError:pass
    threading.Thread(target=reader,daemon=True).start()
    def send(data):
        proc.stdin.write(json.dumps(data)+'\n');proc.stdin.flush()
    def wait_id(identifier):
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            try:m=messages.get(timeout=max(.01,deadline-time.monotonic()))
            except queue.Empty:break
            if m.get('id')==identifier:
                if 'error' in m:raise RuntimeError(m['error'])
                return m['result']
        raise TimeoutError('Codex skills/list did not return in the bounded verification window')
    try:
        send({'id':1,'method':'initialize','params':{'clientInfo':{'name':'video-workflow-verification','version':'1.0.0'},'capabilities':{'experimentalApi':True}}})
        initialize=wait_id(1)
        send({'method':'initialized'})
        send({'id':2,'method':'skills/list','params':{'cwds':[str(args.cwd.resolve())],'forceReload':True}})
        result=wait_id(2)
        found=[];related_errors=[]
        for row in result.get('data',[]):
            for skill in row.get('skills',[]):
                if skill.get('name') in NAMES:found.append(skill)
            related_errors += [e for e in row.get('errors',[]) if any(n in e.get('path','') for n in NAMES)]
        report={'method':'actual_codex_app_server_skills_list_force_reload','checked_at_utc':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                'initialize':initialize,'cwd':str(args.cwd.resolve()),'skills':found,'related_errors':related_errors,
                'all_three_discovered':NAMES=={s['name'] for s in found},'model_invoked':False,'thread_created':False,
                'limits':'实际本机Codex CLI app-server发现；不是原正在进行的聊天已重新注入metadata的证明'}
        args.out.parent.mkdir(parents=True,exist_ok=True)
        with args.out.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
        print(json.dumps({'all_three_discovered':report['all_three_discovered'],'skills':[{'name':s['name'],'enabled':s.get('enabled'),'scope':s.get('scope')} for s in found],'related_errors':related_errors}))
    finally:
        proc.stdin.close()
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:proc.terminate();proc.wait(timeout=5)
if __name__=='__main__':main()
