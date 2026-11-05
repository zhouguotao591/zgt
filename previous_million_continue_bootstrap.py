"""Create a separately archived second million search; exclude all earlier hashes."""
from pathlib import Path
root=Path(__file__).resolve().parent
for suffix in ('_prepare.py','_runner.cjs','_reference.py','_publish.py','_preflight.cjs','_preflight.py','_verify.py'):
 text=(root/('previous_million'+suffix)).read_text('utf8')
 text=text.replace('previous_million_prepare','previous_million_continue_prepare').replace('previous_million_reference','previous_million_continue_reference').replace('previous_million_runner','previous_million_continue_runner')
 text=text.replace('parameter-search-previous-million','parameter-search-previous-million-continue')
 text=text.replace('202611030700','202611031700').replace('百万组评分优选','续百万组评分优选')
 # Existing tests apply to the unchanged rule; do not claim a fresh test run here.
 text=text.replace(",project_tests={'passed':48,'failures':0}","")
 (root/('previous_million_continue'+suffix)).write_text(text,encoding='utf8')
print('Continuation scripts ready')
