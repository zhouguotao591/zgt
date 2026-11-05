import csv,io,json,zipfile,hashlib
from pathlib import Path
from .model import parse_time,utcnow,strict_int,convert

def csv_records(data):
 text=data.decode('utf-8-sig');reader=csv.DictReader(io.StringIO(text));required=['DRAW NUMBER','DRAW DATE']+['NUMBER DRAWN '+str(i) for i in range(1,21)]
 if not reader.fieldnames or not set(required)<=set(reader.fieldnames):raise ValueError('CSV缺少官方DRAW NUMBER / DRAW DATE / NUMBER DRAWN 1—20列')
 for n,r in enumerate(reader,2):
  try:yield {'id':int(r['DRAW NUMBER']),'time':parse_time(r['DRAW DATE']).isoformat(),'numbers':[int(r['NUMBER DRAWN '+str(i)]) for i in range(1,21)]}
  except (ValueError,TypeError,KeyError) as e:raise ValueError(f'CSV第{n}行错误: {e}') from e

def path_value(obj,path):
 for key in path.split('.') if path else []:
  obj=obj[int(key)] if isinstance(obj,list) else obj[key]
 return obj

def json_records(data,config):
 payload=json.loads(data);mapping=config.get('field_map',{});root=path_value(payload,mapping.get('records',''))
 if isinstance(root,dict):
  for key in ('draws','drawResults','results','data','kenoDraws','previousDraws'):
   if isinstance(root.get(key),list):root=root[key];break
 if isinstance(root,dict):root=[root]
 if not isinstance(root,list):raise ValueError('JSON记录列表无法识别，请配置collector.field_map')
 def pick(r,field,aliases):
  key=mapping.get(field)
  if key:
   try:return path_value(r,key)
   except (KeyError,IndexError,TypeError):pass
  for k in aliases:
   if k in r:return r[k]
  raise ValueError('JSON字段缺失: '+field)
 for r in root:
  did=pick(r,'id',('drawNumber','drawNbr','drawNo','id','drawId'))
  when=pick(r,'time',('drawDate','drawTime','drawDateTime','date','time'))
  balls=pick(r,'numbers',('numbers','drawNumbers','winningNumbers','drawnNumbers'))
  if isinstance(balls,str):balls=balls.replace(',',' ').split()
  if not isinstance(balls,list):raise ValueError('号码字段必须是数组或逗号分隔字符串')
  balls=[strict_int(x.get('number',x.get('value'))) if isinstance(x,dict) else strict_int(x) for x in balls]
  mode=config.get('number_order','require_ascending')
  if mode=='ascending':balls.sort()
  elif mode=='require_ascending' and balls!=sorted(balls):raise ValueError('接口号码未升序；请先核对与官方CSV的列序再配置number_order')
  yield {'id':strict_int(did),'time':parse_time(when).isoformat(),'numbers':balls}

def world_draw_record(draw,config):
 if draw.get('keno') is None:raise ValueError('开奖结果已公布，但上游原始20个Keno号码尚未补齐')
 balls=sorted(strict_int(x) for x in draw['keno'])
 result=convert(balls)
 for name,expected in [('n1',result['a']),('n2',result['b']),('n3',result['c']),('sum',result['total'])]:
  if strict_int(draw[name])!=expected:raise ValueError('备用源ABC换算校验失败: '+name)
 if draw.get('big') is not None and draw['big'] != (result['size']=='大'):raise ValueError('备用源大小校验失败')
 if draw.get('odd') is not None and draw['odd'] != (result['parity']=='单'):raise ValueError('备用源单双校验失败')
 dt=parse_time(draw['time'])
 if dt.tzinfo is None:
  from zoneinfo import ZoneInfo
  dt=dt.replace(tzinfo=ZoneInfo(config.get('fallback_timezone','Asia/Shanghai')))
 return {'id':strict_int(draw['nbr']),'time':dt.isoformat(),'numbers':balls}


def world_records(data,config):
 payload=json.loads(data)
 if payload.get('ok') is not True:raise ValueError('pc28.world返回失败状态')
 data=payload['data']
 draws=[data['draw']] if 'draw' in data else data['items'] if 'items' in data else [data]
 for draw in draws:yield world_draw_record(draw,config)


def work_records(data,config):
 payload=json.loads(data);draw=payload['data']
 if draw.get('kenoNbrs') is None:raise ValueError('pc28.work原始Keno号码尚未就绪')
 from zoneinfo import ZoneInfo
 dt=parse_time(draw['drawnAt'])
 if dt.tzinfo is None:raise ValueError('pc28.work时间缺少UTC偏移')
 canonical={'nbr':draw['drawNbr'],'time':dt.astimezone(ZoneInfo('Asia/Shanghai')).isoformat(),'keno':draw['kenoNbrs'],'n1':draw['n1'],'n2':draw['n2'],'n3':draw['n3'],'sum':draw['drawNum'],'big':draw['size']=='大','odd':draw['parity']=='单'}
 yield world_draw_record(canonical,config)


def decode_records(data,config):
 if config.get('adapter')=='pc28_work':
  yield from work_records(data,config)
 elif config.get('adapter')=='pc28_world':
  yield from world_records(data,config)
 elif data[:2]==b'PK':
  z=zipfile.ZipFile(io.BytesIO(data));names=[n for n in z.namelist() if n.lower().endswith('.csv') and not n.startswith('__MACOSX')]
  if not names:raise ValueError('ZIP中没有CSV')
  if sum(z.getinfo(n).file_size for n in names)>256*1024*1024:raise ValueError('解压内容超过256MB')
  for n in names:yield from csv_records(z.read(n))
 elif data.lstrip()[:1] in (b'{',b'['):yield from json_records(data,config)
 else:yield from csv_records(data)

def archive_response(root,data):
 digest=hashlib.sha256(data).hexdigest();folder=Path(root)/'data/raw';folder.mkdir(parents=True,exist_ok=True);path=folder/(digest+'.bin')
 if not path.exists():path.write_bytes(data)
 return {'sha256':digest,'raw_file':str(path)}


def import_bytes(db,root,data,source,config):
 proof=archive_response(root,data)
 result=db.import_records(decode_records(data,config),source,source_timezone=config.get('source_timezone'))
 return {**result,**proof}


def import_file(db,root,path,config):
 p=Path(path)
 if not p.is_file() or p.stat().st_size>128*1024*1024:raise ValueError('文件不存在或超过128MB')
 started=utcnow()
 try:
  result=import_bytes(db,root,p.read_bytes(),'file:'+p.name,config);db.log_job('import','ok',result,started);return result
 except Exception as e:db.log_job('import','error',{'error':str(e)},started);raise


def reported_result(data,adapter,config):
 payload=json.loads(data)
 if adapter=='pc28_world':
  if payload.get('ok') is not True:raise ValueError('pc28.world返回失败状态')
  d=payload['data']['draw'];did=strict_int(d['nbr']);dt=parse_time(d['time'])
  if dt.tzinfo is None:
   from zoneinfo import ZoneInfo
   dt=dt.replace(tzinfo=ZoneInfo(config.get('fallback_timezone','Asia/Shanghai')))
  total=strict_int(d['sum'])
 elif adapter=='pc28_work':
  d=payload['data'];did=strict_int(d['drawNbr']);dt=parse_time(d['drawnAt'])
  if dt.tzinfo is None:raise ValueError('pc28.work开奖时间缺少时区')
  total=strict_int(d['drawNum'])
 else:raise ValueError('不支持的三球核对来源')
 digits=[strict_int(d[k]) for k in ('n1','n2','n3')]
 if did<=0 or any(not 0<=n<=9 for n in digits) or sum(digits)!=total:raise ValueError('三球结果范围或和值校验失败')
 from zoneinfo import ZoneInfo
 return {'id':did,'time':dt.astimezone(ZoneInfo('Asia/Shanghai')).isoformat(),'a':digits[0],'b':digits[1],'c':digits[2],'total':total,'numbers':None}


def crosscheck_results(left,right,sources):
 if [left[k] for k in ('id','a','b','c','total')]!=[right[k] for k in ('id','a','b','c','total')]:raise ValueError('两个来源的期号或三个结果号码不一致，拒绝采用')
 if abs((parse_time(left['time'])-parse_time(right['time'])).total_seconds())>30:raise ValueError('同一期双源时间差超过30秒，拒绝采用')
 return {**right,'verification':'crosschecked_abc','result_sources':sources}
