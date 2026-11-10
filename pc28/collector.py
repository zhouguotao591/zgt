import urllib.request,json
from urllib.parse import urlparse
from .ingest import import_bytes,archive_response,decode_records,reported_result,crosscheck_results
from .model import utcnow,parse_time
from datetime import datetime,timezone


def fetch(url,config):
 parsed=urlparse(url)
 if parsed.scheme!='https' or not parsed.hostname:raise ValueError('数据源仅支持HTTPS URL')
 proxy=config.get('proxy','');opener=urllib.request.build_opener(urllib.request.ProxyHandler({'https':proxy,'http':proxy}) if proxy else urllib.request.ProxyHandler())
 req=urllib.request.Request(url,headers={'User-Agent':'PC28LocalResearch/1.0','Accept':'application/json,application/zip,text/csv;q=0.9,*/*;q=0.1','Cache-Control':'no-cache','Pragma':'no-cache'})
 limit=int(config.get('max_response_mb',64))*1024*1024
 with opener.open(req,timeout=config['timeout_seconds']) as r:
  body=r.read(limit+1)
  if len(body)>limit:raise ValueError('响应超过配置的大小限制')
  if not body:raise ValueError('数据源返回空响应')
  if body.lstrip().lower().startswith((b'<!doctype',b'<html')):raise ValueError('数据源返回HTML，可能为拦截页或接口变更')
  return body


def collect_once(db,root,config,archive=False):
 started=utcnow()
 if archive:
  url=config['archive_url']
  try:
   body=fetch(url,config);result=import_bytes(db,root,body,url,config)
   result.update(gaps=db.gaps(),selected_source='official_archive',url=url)
   db.log_job('archive','ok',result,started);return result
  except Exception as e:
   db.log_job('archive','error',{'error':str(e),'url':url},started);raise
 attempts=[];candidate=None;candidate_source=None
 sources=[('official',config['latest_url'],config)]
 if config.get('fallback_enabled',False):
  fallback_config={**config,'adapter':'pc28_world','proxy':config.get('fallback_proxy','')}
  sources.append(('fallback',config['fallback_url'],fallback_config))
 if config.get('work_fallback_enabled',False):
  sources.append(('pc28_work',config['work_fallback_url'],{**config,'adapter':'pc28_work','proxy':config.get('work_fallback_proxy','')}))
 for name,url,source_config in sources:
  attempt_start=utcnow()
  try:
   body=fetch(url,source_config)
   proof=archive_response(root,body);partial=False
   if name=='fallback' and config.get('allow_crosschecked_results',False):
    payload=json.loads(body)
    if payload.get('ok') is True and payload.get('data',{}).get('draw',{}).get('keno') is None:
     candidate=reported_result(body,'pc28_world',config);candidate_source=url
     attempts.append({'source':name,'url':url,'status':'waiting_raw','error':'原始20码未就绪，等待与pc28.work核对三球'})
     db.log_job('latest_'+name,'waiting_raw',{'draw':candidate['id'],'proof':proof},attempt_start)
     continue
   if name=='pc28_work' and config.get('allow_crosschecked_results',False) and json.loads(body).get('data',{}).get('kenoNbrs') is None:
    if candidate is None:raise ValueError('pc28.work缺少20码且没有可核对的另一来源，暂不采用')
    right=reported_result(body,'pc28_work',config);records=[crosscheck_results(candidate,right,[candidate_source,url])];partial=True
   else:records=list(decode_records(body,source_config))
   if not records:raise ValueError('数据源没有返回有效开奖记录')
   if name=='pc28_work' and candidate and candidate['id']==records[0]['id']:
    right=reported_result(body,'pc28_work',config);crosscheck_results(candidate,right,[candidate_source,url])
   publication_status='ok';inferred_plan=None;stale_seconds=0
   latest=max(records,key=lambda r:r['id']);dt=parse_time(latest['time'])
   if name in ('fallback','pc28_work') and dt.tzinfo:
    stale_seconds=(datetime.now(timezone.utc)-dt).total_seconds()
    if stale_seconds>210:
     from .timing import infer_break
     current=db.tail(1)
     if current and current[-1]['id']==latest['id'] and current[-1].get('draw_utc') and abs((parse_time(current[-1]['draw_utc'])-dt).total_seconds())<=60:inferred_plan=infer_break(db)
     publication_status='resting' if inferred_plan and inferred_plan['resting'] else 'stale'
     if publication_status=='stale' and name=='fallback' and config.get('work_fallback_enabled',False):raise ValueError('备用源仍停留在上一期，继续查询pc28.work')
   result={**db.import_records(records,url,source_timezone=source_config.get('source_timezone')),**proof}
   result['verification']='crosschecked_abc' if partial else 'full_keno'
   if result['conflicts']:raise ValueError('开奖与已有号码存在冲突，已隔离等待核对')
   attempts.append({'source':name,'url':url,'status':publication_status})
   from .timing import extract_schedule
   plan=inferred_plan if publication_status=='resting' else None if publication_status=='stale' else extract_schedule(body,max(r['id'] for r in records))
   if plan and not plan.get('inferred'):plan['source']=name
   result.update(gaps=db.gaps(),selected_source=name,url=url,attempts=attempts,draw_schedule=plan,publication_status=publication_status,stale_seconds=max(0,int(stale_seconds)))
   if name=='fallback':result['attribution']='数据来自 pc28.world https://pc28.world'
   if name=='pc28_work':result['attribution']='数据来源 https://www.pc28.work/'
   db.log_job('latest_'+name,publication_status,result,attempt_start)
   db.log_job('latest',publication_status,result,started)
   return result
  except Exception as e:
   failure={'source':name,'url':url,'status':'error','error':str(e)}
   attempts.append(failure);db.log_job('latest_'+name,'error',failure,attempt_start)
 db.log_job('latest','error',{'attempts':attempts},started)
 raise RuntimeError('所有开奖源采集失败：'+'；'.join(a['source']+': '+a.get('error','') for a in attempts))


def repair_raw_once(db,root,config):
 from .ingest import world_draw_record
 from urllib.parse import urlsplit,urlunsplit,urlencode
 pending=db.query("SELECT id FROM draws WHERE verification='crosschecked_abc' ORDER BY id LIMIT 200")
 if not pending:return {'pending':0,'upgraded':0}
 target={r['id'] for r in pending};parts=urlsplit(config['fallback_url']);url=urlunsplit((parts.scheme,parts.netloc,'/api/v1/draws',urlencode({'limit':200,'before':min(target)+1}),''))
 body=fetch(url,{**config,'proxy':config.get('fallback_proxy','')});proof=archive_response(root,body);payload=json.loads(body)
 if payload.get('ok') is not True:raise ValueError('原始号码补采接口返回失败')
 records=[world_draw_record(d,config) for d in payload['data']['items'] if d['nbr'] in target and d.get('keno') is not None]
 result={**db.import_records(records,url),**proof};result['pending']=db.query("SELECT COUNT(*) n FROM draws WHERE verification='crosschecked_abc'")[0]['n'];db.log_job('repair_raw','ok',result);return result


def repair_pending_once(db,root,config):
 """Recover missing results blocking settlement; never infer a win or cancel a stake."""
 from .ingest import work_records
 from urllib.parse import urlsplit,urlunsplit,urlencode
 targets={r['target_draw'] for r in db.query("SELECT target_draw FROM trend_orders WHERE status='pending' AND target_draw NOT IN (SELECT id FROM draws) UNION SELECT target_draw FROM orders WHERE status='pending' AND target_draw NOT IN (SELECT id FROM draws) UNION SELECT target_draw FROM custom_orders WHERE status='pending' AND target_draw NOT IN (SELECT id FROM draws)")}
 latest=db.tail(1)
 targets={i for i in targets if latest and i<=latest[-1]['id']}
 if not targets:return {'missing':[],'inserted':0}
 if not config.get('work_fallback_enabled'):return {'missing':sorted(targets),'inserted':0,'reason':'pc28.work备用源未启用'}
 parts=urlparse(config['work_fallback_url']);base=urlunsplit((parts.scheme,parts.netloc,'/bff/market/history','',''));inserted=0
 for page in range(1,11):
  url=base+'?'+urlencode({'page':page,'pageSize':200})
  body=fetch(url,{**config,'proxy':config.get('work_fallback_proxy','')});archive_response(root,body);payload=json.loads(body);items=payload['items']
  if not items:break
  ids=[int(d['drawNbr']) for d in items]
  if ids!=sorted(set(ids),reverse=True):raise ValueError('历史源期号非唯一降序')
  records=[]
  for item in items:
   if item['drawNbr'] in targets and item.get('kenoNbrs') is not None:records.extend(work_records(json.dumps({'data':item}).encode(),config))
  result=db.import_records(records,url);inserted+=result['inserted']
  targets-={r['id'] for r in records}
  if not targets or min(ids)<=min(targets):break
 result={'inserted':inserted,'missing':sorted(targets)};db.log_job('repair_pending','ok' if not targets else 'incomplete',result);return result
