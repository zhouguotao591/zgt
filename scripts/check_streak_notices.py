"""Isolated HTTP validation; no real Telegram credentials or network sends."""
import sys
from pathlib import Path
from unittest.mock import patch
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/'tests')]
from test_trend_risk_http import TrendHTTPTests
from test_robot_signals import MemorySecret
from pc28.notifications import Notifications

f=TrendHTTPTests();f.setUp()
try:
 f.setup()
 with patch.object(Notifications,'secret',return_value=MemorySecret()):
  body=dict(name='群组连续9期测试',mode='fixed',chat_id='-1001234567890',token='123456:abcdefghijklmnopqrstuvwx',enabled=True,events=['streak'])
  status,data=f.admin.call('/api/admin/robot-notifications',body)
  assert status==200,(status,data)
  body['id']=data['id'];body['token']=''
  status,data=f.admin.call('/api/admin/robot-notifications')
  assert status==200 and data['subscriptions'][0]['events']==['streak'],data
  body['events']=['streak','market']
  assert f.admin.call('/api/admin/robot-notifications',body)[0]==200
  status,html=f.admin.call('/admin')
  assert status==200 and 'value="streak"' in html and '仅第9期提醒一次' in html
 print('PASS: admin form, create group subscription, persist category, edit without replacing token')
finally:f.tearDown()
