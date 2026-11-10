import unittest
from zoneinfo import ZoneInfo
from pc28.tools import enrich,metrics,analyze_number,detect_dragons,coverage,daily_summary,Tools

def row(id,total=13,a=5,b=1,c=7):
 return enrich({'id':id,'source_time':'2026-01-01T00:00:00','draw_utc':'2026-01-01T16:30:00+00:00','a':a,'b':b,'c':c,'total':total,'size':'大' if total>=14 else '小','parity':'单' if total%2 else '双','numbers':'null','source':'test','verification':'crosschecked_abc'},ZoneInfo('America/Vancouver'))
class ToolTests(unittest.TestCase):
 def test_home_daily_summary_counts_and_filters_old_dates(self):
  rows=[{**row(1,13),'date':'2026-01-01','beijing_time':'2026-01-01 23:59:00'},
        row(2,14),{**row(3,15),'beijing_time':'2026-01-02 01:00:00'}]
  tools=Tools(None);tools.daily=daily_summary(rows);tools.snapshot=lambda:rows
  home=tools.run({'view':'home','limit':'1'})
  self.assertEqual(home['total'],1)
  self.assertEqual(home['daily']['total'],2)
  self.assertEqual(home['daily']['rows'][0],{'date':'2026-01-02','count':2,
                   '大':2,'小':0,'单':1,'双':1,'start':'00:30','end':'01:00'})
  older=tools.run({'view':'home','limit':'1','date':'2026-01-01'})
  self.assertEqual([r['id'] for r in older['rows']],[1])
  self.assertEqual(older['daily'],home['daily'])
 def test_daily_page_size_and_last_page(self):
  tools=Tools(None);tools.snapshot=lambda:[]
  tools.daily=[{'date':f'2026-01-{day:02d}'} for day in range(31,0,-1)]
  first=tools.run({'view':'home','daily_size':'20'})['daily']
  last=tools.run({'view':'home','daily_size':'20','daily_page':'99'})['daily']
  self.assertEqual((first['page'],first['pages'],len(first['rows'])),(1,2,20))
  self.assertEqual((last['page'],last['page_size'],len(last['rows'])),(2,20,11))
  with self.assertRaises(ValueError):tools.run({'view':'home','daily_size':'19'})
 def test_attributes_and_beijing(self):
  r=row(1,13,5,1,7);self.assertEqual(r['date'],'2026-01-02');self.assertEqual(r['combo'],'小单');self.assertEqual(r['dragon'],'虎');self.assertEqual(r['position'],'中');self.assertEqual(r['shape'],'杂六')
  self.assertIn('A:大',r['_keys']);self.assertIn('B:小单',r['_keys'])
 def test_shape_rules_no_wrap(self):
  self.assertEqual(row(1,17,0,8,9)['shape'],'杂六');self.assertEqual(row(1,9,2,3,4)['shape'],'顺子');self.assertEqual(row(1,3,1,1,1)['shape'],'豹子')
 def test_counts_and_omissions(self):
  rs=[row(1,13),row(2,14),row(3,14)];g=metrics(rs,'regular');items={x['key']:x for y in g for x in y['items']};self.assertEqual(items['小']['count'],1);self.assertEqual(items['小']['current'],2);self.assertEqual(items['大']['current'],0);self.assertEqual(sum(items[f'{n:02}']['count'] for n in range(28)),3)
 def test_gaps_reset_current_omission(self):
  items={x['key']:x for g in metrics([row(1,13),row(3,14)],'regular') for x in g['items']};self.assertEqual(items['小']['current'],1);self.assertTrue(items['小']['at_least']);self.assertEqual(coverage([row(1),row(3)])['gaps'],1)
 def test_last_draw_no_future_sample(self):
  a=analyze_number([row(1,13),row(2,14),row(3,13)],13);self.assertEqual(a['hits'],2);self.assertEqual(a['successor_count'],1);self.assertEqual(a['next_attributes'][0]['count'],1);self.assertEqual(a['average_interval'],1.5)
 def test_gap_not_counted_as_next_draw(self):
  a=analyze_number([row(1,13),row(3,14)],13);self.assertEqual(a['successor_count'],0)
 def test_ball_groups_denominators(self):
  groups=metrics([row(1),row(2)],'balls');self.assertTrue(all(sum(x['count'] for x in g['items'])==2 for g in groups[:3]))
 def test_dragon_active_and_gap_break(self):
  rs=[row(i,13) for i in range(1,7)];alerts=detect_dragons(rs,5,'single');self.assertTrue(any(x['dimension']=='大小' and x['active'] and x['length']==6 for x in alerts));self.assertFalse(detect_dragons([row(1),row(2),row(10),row(11)],5,'single'))
 def test_alternating_cycle(self):
  rs=[row(i,13 if i%2 else 14) for i in range(1,9)];alerts=detect_dragons(rs,5,'jump');self.assertTrue(any(x['dimension']=='大小' and x['length']==8 and x['active'] for x in alerts))
 def test_dragon_filters_only_return_requested_kind(self):
  rs=[row(i,13) for i in range(1,12)];self.assertTrue(all(a['kind']=='single' for a in detect_dragons(rs,5,'single')));self.assertTrue(all(a['kind']=='odds' for a in detect_dragons(rs,5,'odds')))
 def test_empty_analysis(self):
  a=analyze_number([],0);self.assertIsNone(a['rate']);self.assertEqual(a['successor_count'],0);self.assertTrue(all(x['eligible']==0 for x in a['recovery']))
if __name__=='__main__':unittest.main()
