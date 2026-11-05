import sys,json,urllib.request,urllib.error
from pathlib import Path
base='http://127.0.0.1:8028'
with urllib.request.urlopen(base+'/') as r:page=r.read().decode();assert 'data-tab="ai-analysis"' in page
with urllib.request.urlopen(base+'/api/ai-analysis') as r:d=json.load(r)
assert [c['axis'] for c in d['cards']]==['size','parity']
for card in d['cards']:
 assert card['validation']['count']>0
 if card['probability'] is not None:assert abs(card['probability']+card['complement']-1)<1e-9 and card['target_draw']==card['cutoff_draw']+1
 assert 'features' not in card and 'model' not in card
for path,expected in [('/api/admin/ai',403),('/admin/api/admin/ai',401),('/ai-admin.js',403)]:
 try:urllib.request.urlopen(base+path);raise AssertionError('Unexpected access')
 except urllib.error.HTTPError as e:assert e.code==expected
for path in ['/ai-analysis.js','/ai-analysis.css']:
 with urllib.request.urlopen(base+path) as r:assert r.status==200
print(json.dumps({'public_ai':200,'training':d['training'],'cards':[{'name':c['name'],'state':c['state'],'target':c['target_draw'],'probability':c['probability'],'validation_samples':c['validation']['count'],'qualified':c['validation']['qualified']} for c in d['cards']],'admin_protected':True},ensure_ascii=False))
