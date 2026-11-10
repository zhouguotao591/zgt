import unittest,json,copy,io,zipfile
from unittest.mock import patch
from pathlib import Path
from pc28.config import DEFAULT
from pc28.collector import fetch
from pc28.ingest import decode_records
class CollectorTests(unittest.TestCase):
 def test_official_json_adapter(self):
  data=json.dumps({'draws':[{'drawNumber':99,'drawDate':'2026-11-01T00:00:00-07:00','numbers':list(range(1,21))}]}).encode();self.assertEqual(list(decode_records(data,DEFAULT['collector']))[0]['id'],99)
 def test_archive_adapter(self):
  header=['DRAW NUMBER','DRAW DATE']+['NUMBER DRAWN '+str(i) for i in range(1,21)];import csv;out=io.StringIO();w=csv.writer(out);w.writerow(header);w.writerow([1,'2026-01-01 00:02:00']+list(range(1,21)));z=io.BytesIO()
  with zipfile.ZipFile(z,'w') as f:f.writestr('KenoCurrentYear.csv',out.getvalue())
  self.assertEqual(list(decode_records(z.getvalue(),DEFAULT['collector']))[0]['numbers'],list(range(1,21)))
 def test_html_rejected(self):
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,n):return b'<!doctype html><html>Access denied</html>'
  with patch('pc28.collector.urllib.request.build_opener') as opener:
   opener.return_value.open.return_value=Response()
   with self.assertRaises(ValueError):fetch('https://www.playnow.com/test',DEFAULT['collector'])
if __name__=='__main__':unittest.main()
