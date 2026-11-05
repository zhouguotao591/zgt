"""Check replay parity and parameter uniqueness before publication."""
import json
from pathlib import Path
p=Path('data/parameter-search-100k');r=json.loads((p/'results.json').read_text('utf8'));checks=json.loads((p/'reference.json').read_text('utf8'));plans=json.loads((p/'plans.json').read_text('utf8'));old=set();errors=[]
for name in ('parameter-search','parameter-search-positive'):
 for x in json.loads((Path('data')/name/'plans.json').read_text('utf8')):old.add((x['source'],x['parameter_sha256']))
fresh={(x['source'],x['parameter_sha256']) for x in plans};assert len(fresh)==500000 and not fresh.intersection(old)
for c in checks:
 for window in ('one','three'):
  for key,value in c[window].items():
   got=r[c['index']][window][key]
   if (value is None)!=(got is None) or value is not None and abs(value-got)>1e-8:errors.append({'index':c['index'],'window':window,'metric':key,'reference':value,'fast':got})
validation={'comparisons':len(checks)*14,'errors':errors,'unique_candidates':len(fresh),'previous_round_duplicates':len(fresh.intersection(old))}
(p/'validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(validation));assert not errors
