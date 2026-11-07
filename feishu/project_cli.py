#!/usr/bin/env python3
"""Feishu Project browser-session maintenance: inspect, preview, apply, verify.

Uses only the user's already-authorized project.feishu.cn session. Auth headers
remain in browser memory. Project-specific IDs, decisions and evidence live in
the business repository, not in this shared tool. See the feishu skill route.
"""
from __future__ import annotations
import argparse,hashlib,json,re,shutil,subprocess,sys,time,os
from pathlib import Path
from datetime import datetime,timezone

def now():return datetime.now(timezone.utc).isoformat()
def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def dump(p,d):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(p)
def digest(d):return hashlib.sha256(json.dumps(d,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def require(ok,message):
 if not ok:raise ValueError(message)

def acquire_batch_lock(folder):
 folder.mkdir(parents=True,exist_ok=True)
 f=(folder/'writer.lock').open('a+b')
 try:
  f.seek(0,2)
  if not f.tell():f.write(b'0');f.flush()
  f.seek(0)
  if os.name=='nt':
   import msvcrt
   msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
  else:
   import fcntl
   fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
 except OSError:
  f.close();raise ValueError('Another process is writing this batch; inspect the active writer first')
 return f

class Browser:
 def __init__(self,session,config,request_id=None):
  self.session=session;self.c=config
  require(bool(shutil.which('browser-act')),'browser-act is not installed')
  if request_id:
   raw=self.cli('network','request',request_id)
   require('url=https://project.feishu.cn/' in raw,'Request is not from project.feishu.cn')
   lines=raw.split('request_headers:',1)[1].split('post_data=',1)[0].split('response_headers:',1)[0].splitlines()
   headers={'Content-Type':'application/json','Locale':'zh','x-content-language':'zh'}
   for line in lines:
    if '=' not in line:continue
    key,value=line.strip().split('=',1)
    if key.lower() in ('x-meego-csrf-token','x-meego-from','x-lark-gw','x-meego-source','x-meego-timezone','x-meego-local'):headers[key]=value
   require('x-meego-csrf-token' in headers,'No observed CSRF header; inspect a fresh project request')
   self.ev('window.__projectHeaders='+json.dumps(headers)+';JSON.stringify({ready:true})')
  self.ev("(()=>{if(location.origin!=='https://project.feishu.cn'||!window.__projectHeaders)throw Error('Open authorized project session and supply --request-id');return JSON.stringify({ready:true});})()")
 def cli(self,*args,js=None):
  p=subprocess.run([shutil.which('browser-act'),'--session',self.session,*args],input=js,text=True,encoding='utf-8',capture_output=True)
  if p.returncode:raise RuntimeError(p.stderr[:500])
  return p.stdout.strip()
 def ev(self,js):return json.loads(self.cli('eval','--stdin',js=js))
 def request(self,method,path,body=None):
  require(path.startswith('/goapi/'),'Only observed project APIs are supported')
  js="""(async()=>{const r=await fetch(PATH,{method:METHOD,headers:window.__projectHeaders,body:BODY,signal:AbortSignal.timeout(30000)});const j=await r.json();return JSON.stringify(j);})()"""
  js=js.replace('PATH',json.dumps(path)).replace('METHOD',json.dumps(method)).replace('BODY','undefined' if body is None else json.dumps(json.dumps(body,ensure_ascii=False)))
  r=self.ev(js);require(r.get('code')==0,f'Project request rejected: code={r.get("code")} msg={r.get("msg")}')
  return r.get('data')
 def item(self,ident,kind='story'):
  return self.request('GET',f'/goapi/v2/projects/{self.c["space"]}/work_item/{kind}/{ident}?')
 def all_names(self):
  c=self.c;p=self.item(c['project_id'],c['project_type']);ids=p[c['project_items_field']]
  # Keep full records in browser memory; return only duplicate-check columns.
  js="""(async()=>{const out=[];for(let n=0;n<IDS.length;n+=6){const rows=await Promise.all(IDS.slice(n,n+6).map(async id=>{const r=await(await fetch('/goapi/v2/projects/'+SPACE+'/work_item/story/'+id+'?',{headers:window.__projectHeaders})).json();if(r.code!==0)throw Error('Project list read failed');return r.data;}));out.push(...rows.map(d=>({id:d.work_item_id,name:d.name,deleted_at:d.deleted_at,parent:d.field_ebb6ab,iteration:d.planning_sprint})));}return JSON.stringify(out);})()"""
  return self.ev(js.replace('IDS',json.dumps(ids)).replace('SPACE',json.dumps(c['space'])))

def validate(c,plan):
 require(plan.get('space')==c['space'] and plan.get('iteration_id')==c['iteration_id'],'Plan targets another space or iteration')
 require(bool(plan.get('authorization')),'Record the user instruction authorizing these changes')
 keys=[];created=set()
 for op in plan['operations']:
  require(op['key'] not in keys,'Duplicate operation key');keys.append(op['key'])
  kind=op['kind'];require(kind in ['create','fields','rename','schedule','advance','delete'],'Unsupported operation')
  require(bool(op.get('reason')),'Every operation needs its decision/evidence reason')
  if kind=='create':
   require(op['fields']['group_type']=='disabled','Creating project chat groups is outside this tool')
   require(op['fields'][c['project_field']]==c['project_id'],'Wrong project')
   require(op['fields'][c['iteration_field']]==[c['iteration_id']],'Wrong iteration')
   if op.get('parent_ref'):require(op['parent_ref'] in created,'Create parent before child')
   created.add(op['key'])
  else:
   require(bool(op.get('id') or op.get('ref')),'Missing exact ID or earlier create reference')
   if op.get('ref'):require(op['ref'] in created,'Reference must name an earlier create operation')
  if kind=='advance':require(op['node'] in c['advance_actions'],'Node outside authorized pre-handoff sequence')
  if kind=='delete':require(bool(op.get('expected_name')),'Delete requires exact expected name')
 return keys

def resolve(op,state):return op.get('id') or state['created'].get(op['key'] if op['kind']=='create' else op.get('ref'))
def scoped(c,d):
 require(not d.get('deleted_at'),'Item is already deleted')
 require(d.get(c['project_field'])==c['project_id'],'Item no longer belongs to target project')
 require(c['iteration_id'] in d.get(c['iteration_field'],[]),'Item no longer belongs to target iteration')
def node(d,key):return next(n for n in d['node_schedules'] if n['state_id']==key)
def field_equal(k,got,want):
 if k=='role_owners':
  norm=lambda rows:{r['role']:sorted(x['username'] for x in r['owners']) for r in rows if r['owners']}
  return norm(got or [])==norm(want)
 if k=='owner':return (got or {}).get('username')==str(want)
 if isinstance(want,dict) and 'doc_text' in want:return (got or {}).get('doc_text','').rstrip()==want['doc_text'].rstrip()
 return got==want
def check(c,op,d):
 kind=op['kind']
 if kind=='delete':return bool(d.get('deleted_at'))
 if kind in ['create','fields']:return all(field_equal(k,d.get(k),v) for k,v in op['fields'].items() if k not in ['template','template_version'])
 if kind=='rename':return d['name']==op['name']
 if kind=='advance':return node(d,op['node'])['status']==3
 if kind=='schedule':
  n=node(d,op['node']);s=op['schedule']['schedule']
  return n.get('estimate_start_time')==s['estimate_start_time'] and n.get('estimate_end_time')==s['estimate_finish_time'] and n.get('points') is None
 return False

def apply_one(b,c,op,d,state):
 kind=op['kind'];space=c['space'];ident=resolve(op,state)
 if kind=='create':
  fields=dict(op['fields'])
  if op.get('parent_ref'):
   require(op['parent_ref'] in state['created'],'Parent has no verified ID');fields[c['parent_field']]=state['created'][op['parent_ref']]
  templates=b.request('GET',f'/goapi/v1/projects/{space}/templates/basic?with_stage_manage=false&work_item_type=story')
  t=next(t for t in templates if t['id']==c['template_id'])
  require(not t['is_disabled'] and t['version']==c['template_version'],'Template changed; inspect current form before creating')
  matches=[x for x in b.all_names() if not x.get('deleted_at') and (x['name']==fields['name'] or re.match(r'^(\d+(?:\.\d+)?)',x['name']) and re.match(r'^(\d+(?:\.\d+)?)',x['name']).group(1)==op['number'])]
  require(not matches,f'Existing matching item: {matches}; inspect rather than duplicate')
  source=b.item(c['create_source_id']);require(source['template']['id']==c['template_id'],'Creation source uses another workflow')
  payload={'project_key':space,'story_id':c['create_source_id'],'remote_project':space,'copy_type':0,'remote_work_item_type_key':'story','work_item_type_key':'story','field_values':[{'field_key':k,'field_value':v if isinstance(v,str) else json.dumps(v,ensure_ascii=False)} for k,v in fields.items()],'template_id':c['template_id'],'copy_num':1,'chat_group_type':2,'previewed_before_creation':True,'role_info':c.get('role_info',{'add_roles':[],'delete_roles':[]})}
  r=b.request('POST','/goapi/v5/space_relation/sync_create',payload)
  ident=r['sync_create_result']['remote_story_id'];state['created'][op['key']]=ident
  return ident,{**op,'fields':fields}
 scoped(c,d)
 if op.get('expected_name'):require(d['name']==op['expected_name'],'Name changed since reviewed plan')
 if op.get('expected_updated_at') and not any(x.get('id')==ident and x['phase']=='verified' for x in state['journal']):require(d['updated_at']==op['expected_updated_at'],'Item changed since snapshot; re-plan this item')
 if kind=='fields':
  for k,v in op.get('expected_fields',{}).items():require(d.get(k)==v,f'Concurrent field edit: {k}')
  b.request('PATCH',f'/goapi/v3/projects/{space}/work_item/story/{ident}',{'field_patch':[{'op':'replace','path':'/'+k,'value':v} for k,v in op['fields'].items()]})
 elif kind=='rename':
  b.request('POST','/goapi/v5/workitem/v1/ui_data/update_value',{'project_key':space,'work_item_type_key':'story','work_item_id':ident,'update_ui_data':[{'uuid':'name','ui_type':'text','update_value':{'text':{'iconUrl':c['name_icon'],'filterValue':None,'value':op['name'],'isMulti':False,'workItemName':'PRD需求'}},'extra_info':{'item_uuid':'brief:item:name'}}],'sync_cal_field_keys':[]})
 elif kind=='schedule':
  path=f'/goapi/v2/projects/{space}/instance/workflow/{ident}/nodes/schedule'
  payload={'node_schedules':{op['node']:op['schedule']},'no_need_latest_work_item':True}
  b.request('PUT',path,payload)
  # The first date assignment calculates points from duration even when null
  # was requested. Clear that observed value once; never retry unknown writes.
  n=node(b.item(ident),op['node']);s=op['schedule']['schedule']
  require(n.get('estimate_start_time')==s['estimate_start_time'] and n.get('estimate_end_time')==s['estimate_finish_time'],'Schedule dates differ after write; inspect before further changes')
  if s.get('points') is None and n.get('points') is not None:
   state['journal'].append({'at':now(),'key':op['key'],'id':ident,'phase':'observed_auto_points','points':n['points']})
   b.request('PUT',path,payload)
 elif kind=='advance':
  require(node(d,op['node'])['status']==2,'Only current active node can be completed')
  require(node(d,c['handoff_node'])['status']!=3,'Handoff node already completed; do not alter workflow')
  require(d['template']['id']==c['template_id'],'Unexpected workflow')
  uuid=f'{space}:story:{c["workflow_key"]}:{op["node"]}:{c["advance_actions"][op["node"]]}'
  b.request('POST','/goapi/v5/workitem/v1/ui_data/update_value',{'project_key':space,'work_item_type_key':'story','work_item_id':ident,'update_ui_data':[{'uuid':uuid,'ui_type':'nodeButton','update_value':{'nodeButton':{'actions':{}}},'extra_info':{'item_uuid':uuid,'resource_type':'control'}}],'sync_cal_field_keys':[]})
 elif kind=='delete':
  require(d.get(c['iteration_field'])==[c['iteration_id']],'Item is shared with another iteration; do not globally delete it')
  for k in c['delete_blocking_fields']:require(not d.get(k),f'Deletion would affect related work: {k}')
  require(not any(n.get('sub_tasks') or n.get('merge_requests') for n in d.get('states',[])),'Node has tasks or merge requests')
  require(set(d.get(c['review_field'],[]))==set(op.get('review_ids',[])),'Review associations changed; re-inspect')
  b.request('DELETE',f'/goapi/v2/projects/{space}/work_item/story/{ident}?cascade_sub_instance=true',{})
 return ident,op

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['inspect','preview','apply','verify']);p.add_argument('--config',required=True);p.add_argument('--session');p.add_argument('--request-id');p.add_argument('--plan');p.add_argument('--state');p.add_argument('--out');p.add_argument('--only');p.add_argument('--id',type=int);p.add_argument('--type',default='story');a=p.parse_args();c=read(a.config)
 if a.command=='preview':
  plan=read(a.plan);validate(c,plan);require(bool(a.state),'--state required')
  dest=Path(a.state)/'preview.json';preview={'at':now(),'plan_sha256':digest(plan),'config_sha256':digest(c),'operations':[{'key':o['key'],'kind':o['kind'],'id':o.get('id'),'ref':o.get('ref'),'name':o.get('name',o.get('fields',{}).get('name',o.get('expected_name'))),'reason':o['reason']} for o in plan['operations']]}
  if dest.exists():
   previous=read(dest)
   require(previous['plan_sha256']==digest(plan) and previous['config_sha256']==digest(c),'Existing state belongs to another plan/config; use a new reviewed batch')
  dump(dest,preview);print(json.dumps({'preview':str(dest),'operations':len(plan['operations']),'online_writes':0},ensure_ascii=False));return
 require(bool(a.session),'--session required for online reads/writes');b=Browser(a.session,c,a.request_id)
 if a.command=='inspect':
  require(bool(a.out),'--out required')
  if a.id:d=b.item(a.id,a.type)
  else:
   iteration=b.item(c['iteration_id'],'sprint');items={}
   for ident in iteration[c['iteration_items_field']]:items[str(ident)]=b.item(ident)
   d={'at':now(),'iteration':iteration,'items':items,'project_items':b.all_names()}
  dump(a.out,d);print(json.dumps({'saved':a.out,'items':len(d.get('items',{})),'name':d.get('name')},ensure_ascii=False));return
 plan=read(a.plan);validate(c,plan);folder=Path(a.state);preview=read(folder/'preview.json')
 require(preview['plan_sha256']==digest(plan) and preview['config_sha256']==digest(c),'Plan/config changed since preview; use a new state after reviewing changes')
 writer_lock=acquire_batch_lock(folder) if a.command=='apply' else None
 sf=folder/'state.json';state=read(sf) if sf.exists() else {'plan_sha256':digest(plan),'created':{},'journal':[],'effective':{}}
 require(state['plan_sha256']==digest(plan),'Wrong journal')
 selected=set(a.only.split(',')) if a.only else None
 ops=[o for o in plan['operations'] if selected is None or o['key'] in selected];require(bool(ops),'No matching operations')
 if a.command=='verify':
  failures=[];records={}
  # Verify final value of each field, since a later action may intentionally supersede an earlier one.
  for original in ops:
   op=state['effective'].get(original['key'],original);ident=resolve(op,state)
   if not ident:failures.append({'key':op['key'],'error':'No created ID'});continue
   if ident not in records:records[ident]=b.item(ident)
   d=records[ident]
   if op['kind']=='create':
    op={**op,'fields':{k:v for k,v in op['fields'].items() if not any(resolve(l,state)==ident and l['kind']=='fields' and k in l['fields'] for l in plan['operations'])}}
   if not check(c,op,d):failures.append({'key':op['key'],'error':'Readback mismatch'})
  dump(folder/'verification.json',{'at':now(),'failures':failures,'records':{str(k):v for k,v in records.items()}})
  print(json.dumps({'verified':not failures,'failures':failures},ensure_ascii=False));require(not failures,'Verification failed');return
 for original in ops:
  op=state['effective'].get(original['key'],original);key=op['key'];last=next((j for j in reversed(state['journal']) if j['key']==key),None);ident=resolve(op,state)
  if last and last['phase']=='verified':continue
  d=b.item(ident) if ident else None
  if last and last['phase']=='sent':
   require(d is not None and check(c,op,d),f'Uncertain result {key}; inspect manually; tool will not resend')
  elif d is not None and check(c,op,d):pass
  else:
   if d is not None:dump(folder/'before'/f'{key}.json',d)
   state['journal'].append({'at':now(),'key':key,'id':ident,'phase':'sent'});dump(sf,state)
   ident,op=apply_one(b,c,op,d,state);state['effective'][key]=op;dump(sf,state)
   for attempt in range(4):
    d=b.item(ident)
    if check(c,op,d):break
    time.sleep(.8)
   require(check(c,op,d),f'Write result not verified: {key}; inspect before retrying')
  dump(folder/'after'/f'{key}.json',d);state['journal'].append({'at':now(),'key':key,'id':ident,'phase':'verified'});dump(sf,state)
  print(json.dumps({'key':key,'id':ident,'phase':'verified'},ensure_ascii=False),flush=True)

if __name__=='__main__':
 try:main()
 except (ValueError,RuntimeError,KeyError,StopIteration) as e:
  print(json.dumps({'ok':False,'error':str(e)},ensure_ascii=False),file=sys.stderr);sys.exit(1)
