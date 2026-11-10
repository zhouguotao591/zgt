"""Time-ordered binary probability research. No order creation or external signals."""
import math,hashlib,json,os
from collections import deque,Counter,defaultdict
from datetime import datetime,timedelta,timezone
from statistics import mean,stdev
from zoneinfo import ZoneInfo

VERSION='probability-v1'
WINDOW=200
BJ=ZoneInfo('Asia/Shanghai')

def features(history,axis):
 values=[int(r[axis]==('大' if axis=='size' else '单')) for r in history]
 other='parity' if axis=='size' else 'size';other_label='单' if axis=='size' else '大'
 run=1
 for v in reversed(values[:-1]):
  if v!=values[-1]:break
  run+=1
 av20=sum(values[-20:])/20
 x=[2*v-1 for v in values[-5:]]+[2*sum(values[-n:])/n-1 for n in (5,10,20,50,100,200)]
 x += [(2*values[-1]-1)*min(run,20)/20]
 x += [2*sum(r[other]==other_label for r in history[-n:])/n-1 for n in (20,100)]
 x += [sum(a!=b for a,b in zip(values[-n:],values[-n+1:]))/(n-1) for n in (20,50)]
 totals=[r['total'] for r in history[-20:]];avg=sum(totals)/20
 x += [(avg-13.5)/13.5,math.sqrt(sum((v-avg)**2 for v in totals)/20)/13.5,(totals[-1]-13.5)/13.5]
 x += [(history[-1][k]-4.5)/4.5 for k in ('a','b','c')]
 hour=history[-1]['_time'].astimezone(BJ).hour
 x += [math.sin(hour*math.pi/12),math.cos(hour*math.pi/12)]
 key=f"{values[-1]}:{min(run,8)}:{-1 if av20<.4 else 1 if av20>.6 else 0}"
 return x,key,{'run':run,'last':values[-1],'counts':{str(n):{'positive':sum(values[-n:]),'count':n} for n in (20,50,100,200)}}

def dataset(raw,conflicts=(),as_of=None):
 from .maintenance import covered_gap
 as_of=as_of or datetime.now(timezone.utc);conflicts=set(conflicts);history=deque(maxlen=WINDOW);previous=None;skips=Counter();out=[];valid=0;digest=hashlib.sha256()
 for source in raw:
  try:
   r=dict(source);t=datetime.fromisoformat(r['draw_utc'])
   if r['id'] in conflicts:raise ValueError('conflict')
   if not t.tzinfo or t>as_of:raise ValueError('time')
   if any(type(r[k])!=int or not 0<=r[k]<=9 for k in ('a','b','c')) or r['total']!=r['a']+r['b']+r['c']:raise ValueError('result')
   if r['size']!=('大' if r['total']>=14 else '小') or r['parity']!=('单' if r['total']%2 else '双'):raise ValueError('label')
  except (ValueError,TypeError,KeyError):history.clear();previous=None;skips['invalid_or_conflict']+=1;continue
  r['_time']=t
  if previous and (r['id']!=previous['id']+1 or t<=previous['_time'] or ((t-previous['_time']).total_seconds()>480 and not covered_gap(previous['_time'],t))):history.clear();skips['sequence_reset']+=1
  if len(history)==WINDOW:
   sample={'id':r['id'],'time':t.isoformat()}
   for axis,label in (('size','大'),('parity','单')):
    x,key,_=features(list(history),axis);sample[axis]={'x':x,'key':key,'y':int(r[axis]==label)}
   out.append(sample)
  history.append(r);previous=r;valid+=1;digest.update(json.dumps([r[k] for k in ('id','draw_utc','a','b','c','total')]).encode())
 return out,list(history),{'valid_draws':valid,'samples':len(out),'excluded':dict(skips),'data_sha256':digest.hexdigest()}

def sigmoid(v):return 1/(1+math.exp(-max(-35,min(35,float(v)))))
def logit(p):return math.log(max(1e-8,p)/max(1e-8,1-p))

def fit_logistic(x,y):
 os.environ.setdefault('OPENBLAS_NUM_THREADS','1');os.environ.setdefault('OMP_NUM_THREADS','1')
 import numpy as np
 x=np.asarray(x,dtype=float);y=np.asarray(y,dtype=float);mu=x.mean(axis=0);sd=x.std(axis=0);sd[sd<1e-8]=1
 z=np.column_stack([np.ones(len(x)),(x-mu)/sd]);w=np.zeros(z.shape[1]);penalty=np.eye(len(w))*.02;penalty[0,0]=1e-7
 for _ in range(12):
  p=1/(1+np.exp(-np.clip(z@w,-35,35)));g=z.T@(p-y)/len(y)+penalty@w;h=(z.T*(p*(1-p)))@z/len(y)+penalty
  step=np.linalg.solve(h,g);w-=step
  if np.max(np.abs(step))<1e-6:break
 return {'mean':mu.tolist(),'scale':sd.tolist(),'coef':w.tolist()}

def logistic_probability(model,x):return sigmoid(model['coef'][0]+sum(w*(v-m)/s for w,v,m,s in zip(model['coef'][1:],x,model['mean'],model['scale'])))

def conditional_probability(model,key):
 wins,n=model['conditions'].get(key,[0,0]);return (wins+100*model['baseline'])/(n+100)

def predict(model,x,key):
 base=model['baseline'];conditional=conditional_probability(model,key);p=logistic_probability(model['logistic'],x);a,b=model['calibration'];calibrated=sigmoid(a*logit(p)+b)
 parts=[base,conditional,calibrated];combined=sum(w*p for w,p in zip(model['weights'],parts))
 return max(.001,min(.999,combined)),parts

def fit(train,calibration,axis):
 os.environ.setdefault('OPENBLAS_NUM_THREADS','1');os.environ.setdefault('OMP_NUM_THREADS','1')
 import numpy as np
 y=[r[axis]['y'] for r in train];base=(sum(y)+1)/(len(y)+2);conditions=defaultdict(lambda:[0,0])
 for r in train:
  pair=conditions[r[axis]['key']];pair[0]+=r[axis]['y'];pair[1]+=1
 model={'baseline':base,'conditions':dict(conditions),'logistic':fit_logistic([r[axis]['x'] for r in train],y),'calibration':[1.,0.],'weights':[1.,0.,0.]}
 raw=[logistic_probability(model['logistic'],r[axis]['x']) for r in calibration];labels=np.array([r[axis]['y'] for r in calibration],float)
 z=np.column_stack([np.array([logit(p) for p in raw]),np.ones(len(raw))]);coef=np.array([1.,0.])
 for _ in range(10):
  p=1/(1+np.exp(-np.clip(z@coef,-35,35)));reg=np.diag([.02,.001]);g=z.T@(p-labels)/len(p)+reg@(coef-np.array([1.,0.]));h=(z.T*(p*(1-p)))@z/len(p)+reg;step=np.linalg.solve(h,g);coef-=step;coef[0]=max(0.,min(4.,coef[0]))
  if max(abs(step))<1e-6:break
 model['calibration']=coef.tolist();c=np.array([conditional_probability(model,r[axis]['key']) for r in calibration]);l=1/(1+np.exp(-np.clip(z@coef,-35,35)));best=float('inf')
 for wc in (0,.25,.5,.75,1):
  for wl in (0,.25,.5,.75,1):
   if wc+wl>1:continue
   p=(1-wc-wl)*base+wc*c+wl*l;score=float(np.mean((p-labels)**2))+.00002*(wc+wl)
   if score<best:best=score;model['weights']=[1-wc-wl,wc,wl]
 return model

def metrics(rows):
 if not rows:return {'count':0,'hit_rate':None,'brier':None,'baseline_brier':None,'fair_brier':None,'log_loss':None,'bins':[],'qualified':False}
 n=len(rows);brier=mean((r['p']-r['y'])**2 for r in rows);baseline=mean((r['base']-r['y'])**2 for r in rows);fair=.25
 daily=defaultdict(list);bins=defaultdict(list);months=defaultdict(list)
 for r in rows:
  day=datetime.fromisoformat(r['time']).astimezone(BJ).strftime('%Y-%m-%d');daily[day].append(((r['base']-r['y'])**2-(r['p']-r['y'])**2,.25-(r['p']-r['y'])**2));bins[min(9,int(r['p']*10))].append(r);months[day[:7]].append(r)
 differences=[mean(v[0] for v in values) for values in daily.values()];fair_differences=[mean(v[1] for v in values) for values in daily.values()]
 lower=min(mean(v)-1.96*stdev(v)/math.sqrt(len(v)) for v in (differences,fair_differences)) if len(differences)>1 else -1
 qualified=n>=3000 and len(differences)>=21 and brier<min(baseline,fair) and lower>0
 return {'count':n,'hit_rate':mean(int((r['p']>=.5)==bool(r['y'])) for r in rows),'brier':brier,'baseline_brier':baseline,'fair_brier':fair,'log_loss':mean(-r['y']*math.log(r['p'])-(1-r['y'])*math.log(1-r['p']) for r in rows),'qualified':qualified,'improvement_lower':lower,'days':len(differences),'bins':[{'from':k/10,'to':(k+1)/10,'count':len(v),'predicted':mean(r['p'] for r in v),'observed':mean(r['y'] for r in v)} for k,v in sorted(bins.items())],'months':[{'month':k,'count':len(v),'hit_rate':mean(int((r['p']>=.5)==bool(r['y'])) for r in v),'brier':mean((r['p']-r['y'])**2 for r in v)} for k,v in sorted(months.items())]}

def train(samples,on_progress=None,train_days=90,cal_days=14,test_days=7,folds=6,min_train=2000,min_cal=500,min_test=500):
 if not samples:raise ValueError('有效样本不足，至少需要200期预热以及训练、验证数据')
 end=datetime.fromisoformat(samples[-1]['time']);complete=end.astimezone(BJ).replace(hour=0,minute=0,second=0,microsecond=0);output={};validation=[]
 for aindex,axis in enumerate(('size','parity')):
  tested=[];fold_reports=[]
  for index in range(folds):
   e=complete-timedelta(days=(folds-1-index)*test_days);t=e-timedelta(days=test_days);c=t-timedelta(days=cal_days);s=c-timedelta(days=train_days)
   tr=[r for r in samples if s<=datetime.fromisoformat(r['time'])<c];ca=[r for r in samples if c<=datetime.fromisoformat(r['time'])<t];te=[r for r in samples if t<=datetime.fromisoformat(r['time'])<e]
   if len(tr)<min_train or len(ca)<min_cal or len(te)<min_test or datetime.fromisoformat(samples[0]['time'])>s+timedelta(days=1):continue
   if on_progress:on_progress(int((aindex*folds+index)/(2*folds)*85),f"{'大小' if axis=='size' else '单双'}滚动验证 {index+1}/{folds}")
   model=fit(tr,ca,axis);records=[]
   for row in te:
    p,_=predict(model,row[axis]['x'],row[axis]['key']);records.append({'axis':axis,'id':row['id'],'time':row['time'],'p':p,'base':model['baseline'],'y':row[axis]['y']})
   tested.extend(records);fold_reports.append({'start':t.isoformat(),'end':e.isoformat(),'training_end':c.isoformat(),'calibration_end':t.isoformat(),'train_count':len(tr),'calibration_count':len(ca),**metrics(records)})
  c=end-timedelta(days=cal_days);s=c-timedelta(days=train_days);tr=[r for r in samples if s<=datetime.fromisoformat(r['time'])<c];ca=[r for r in samples if c<=datetime.fromisoformat(r['time'])<=end]
  if len(tr)<min_train or len(ca)<min_cal or datetime.fromisoformat(samples[0]['time'])>s+timedelta(days=1):raise ValueError('训练数据不足：默认需要90天训练、14天校准；不足时暂不输出概率')
  if on_progress:on_progress(88+aindex*5,f"训练当前{'大小' if axis=='size' else '单双'}模型")
  final=fit(tr,ca,axis);output[axis]={'model':final,'evaluation':metrics(tested),'folds':fold_reports,'training':{'start':s.isoformat(),'calibration_start':c.isoformat(),'end':end.isoformat(),'train_count':len(tr),'calibration_count':len(ca)}};validation.extend(tested)
 return output,validation
