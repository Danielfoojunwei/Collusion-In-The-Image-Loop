import json,glob,os,sys
out=[]
for att in sorted(glob.glob('attempts/r*/a*')):
    r=att.split('/')[1]
    stages=os.path.join(att,'stages')
    if not os.path.isdir(stages) or len(os.listdir(stages))<10: continue
    rec={'round':r,'attempt':att.split('/')[2],'stages':{}}
    for f in sorted(os.listdir(stages)):
        d=json.load(open(os.path.join(stages,f)))['body']
        acts=[]
        for a in d.get('actions',[]) or []:
            nm=a.get('native_message') or {}
            acts.append({'action':a.get('action'),'args':a.get('arguments'),
                         'reasoning':(nm.get('reasoning') or '')[:3000],'content':(nm.get('content') or '')[:1500] if isinstance(nm.get('content'),str) else None,'status':a.get('status')})
        rec['stages'][f[:-5]]={'actions':acts,'parsed':d.get('parsed'),'keys':[k for k in d if k not in('actions',)]}
    out.append(rec)
json.dump(out,open(sys.argv[1],'w'),indent=1)
print(len(out))
for rec in out:
    print(rec['round'],rec['attempt'],{k:[a['action'] for a in v['actions']] for k,v in rec['stages'].items() if v['actions']})
