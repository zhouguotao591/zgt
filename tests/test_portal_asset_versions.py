import hashlib,re,unittest
import test_auth as fixture

class PortalVersionTests(unittest.TestCase):
 setUp=fixture.AuthTests.setUp
 tearDown=fixture.AuthTests.tearDown
 setup=fixture.AuthTests.setup

 def test_admin_assets_versioned_and_changed_script_gets_new_url(self):
  self.setup();status,html=self.admin.call('/admin');self.assertEqual(status,200)
  for name in ('app.js','strategies.js','member-admin.js','admin-bootstrap.js'):
   path=self.root/'web'/name;version=hashlib.sha256(path.read_bytes()).hexdigest()[:12]
   self.assertIn('/admin/assets/'+name+'?v='+version,html)
  old=re.search(r'member-admin\.js\?v=([a-f0-9]+)',html)[1]
  path=self.root/'web/member-admin.js';path.write_bytes(path.read_bytes()+b'\n')
  _,html=self.admin.call('/admin');new=re.search(r'member-admin\.js\?v=([a-f0-9]+)',html)[1]
  self.assertNotEqual(old,new)
  status,script=self.admin.call('/admin/assets/member-admin.js?v='+new)
  self.assertEqual(status,200);self.assertIn('refreshOrders',script)

if __name__=='__main__':unittest.main()
