"""Compare cursor-paginated source history with local IDs and fill verified gaps."""
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,urlencode
import hashlib,json
from .collector import fetch
from .ingest import world_draw_record
from .model import utcnow


def sync_history(db,root,config,max_pages=20):
 if not 1<=int(max_pages)<=100:raise ValueError('最多允许1—100页')
 local={r['id']:r for r in db.rows()}
 if not local:raise ValueError('历史同步需要已有本地数据；首次请先导入官方历史文件')
 first=min(local);before_state={'count':len(local),'first':first,'last':max(local),'gaps':db.gaps(100000)}
 stamp=datetime.now().strftime('%Y%m%d-%H%M%S');folder=Path(root)/'data/exports';folder.mkdir(parents=True,exist_ok=True);backup=folder/f'before-history-sync-{stamp}.sqlite3';db.backup(backup)
 base=urlsplit(config['fallback_url']);base=urlunsplit((base.scheme,base.netloc,'/api/v1/draws','',''))
 source_config={**config,'proxy':config.get('fallback_proxy','')};cursor=None;cutoff=None;results=[];matched=0;missing_raw=[];derived_only=0;mismatches=[];inserted=duplicates=conflicts=0
 # Stop when all IDs from the original local beginning through the initial upstream cutoff exist.
 for _ in range(int(max_pages)):
  params={'limit':200}
  if cursor is not None:params['before']=cursor
  url=base+'?'+urlencode(params);body=fetch(url,source_config);sha=hashlib.sha256(body).hexdigest();raw=Path(root)/'data/raw'/(sha+'.bin');raw.parent.mkdir(parents=True,exist_ok=True)
  if not raw.exists():raw.write_bytes(body)
  payload=json.loads(body)
  if payload.get('ok') is not True:raise ValueError('历史源返回失败状态')
  items=payload['data']['items']
  if not items:break
  ids=[int(d['nbr']) for d in items]
  if len(set(ids))!=len(ids) or ids!=sorted(ids,reverse=True):raise ValueError('历史页期号不唯一或非降序')
  if cursor is not None and max(ids)>=cursor:raise ValueError('历史接口游标没有向前推进')
  if cutoff is None:cutoff=max(ids)
  records=[]
  for draw in items:
   did=int(draw['nbr'])
   if did<first:continue
   old=local.get(did)
   if draw.get('keno') is None:
    if old and [old['a'],old['b'],old['c'],old['total']]==[draw['n1'],draw['n2'],draw['n3'],draw['sum']]:derived_only+=1
    else:missing_raw.append(did)
    continue
   r=world_draw_record(draw,config)
   if old:
    if (json.loads(old['numbers']) is not None and json.loads(old['numbers'])!=r['numbers']) or [old['a'],old['b'],old['c'],old['total']]!=[draw['n1'],draw['n2'],draw['n3'],draw['sum']]:mismatches.append(did)
    else:matched+=1
   records.append(r)
  result=db.import_records(sorted(records,key=lambda x:x['id']),url)
  inserted+=result['inserted'];duplicates+=result['duplicates'];conflicts+=result['conflicts'];results.append({'url':url,'first':min(ids),'last':max(ids),'sha256':sha,**result})
  current={r['id'] for r in db.query('SELECT id FROM draws WHERE id BETWEEN ? AND ?',(first,cutoff))}
  missing=[i for i in range(first,cutoff+1) if i not in current]
  if not missing:break
  if min(ids)<=min(missing):break
  cursor=min(ids)
 after=db.query('SELECT COUNT(*) count,MIN(id) first,MAX(id) last FROM draws')[0]
 report={'created':utcnow(),'cutoff':cutoff,'before':before_state,'after':after,'inserted':inserted,'duplicates':duplicates,'full_overlap_matched':matched,'derived_only_matched':derived_only,'mismatches':mismatches,'conflicts':conflicts,'missing_raw_ids':sorted(set(missing_raw)),'gaps':db.gaps(100000),'pages':results,'backup':str(backup),'note':'按首次历史页最大期号固定核对截止点；只补原本地起始期号之后的数据。未提供20个原始号码的缺失期不伪造入库。来源pc28.world https://pc28.world'}
 report['missing_through_cutoff']=missing if cutoff else []
 name=f'history-sync-{stamp}.json';(folder/name).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');db.log_job('history_sync','ok' if not report['missing_through_cutoff'] and not conflicts else 'incomplete',report)
 return {**report,'file':'/exports/'+name}
