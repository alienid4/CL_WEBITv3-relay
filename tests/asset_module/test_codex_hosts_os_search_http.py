"""New HTTP acceptance: composing text search with existing OS-source/class filters."""
import sys,json,socket
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'APP'/'asset-module'/'backend'))
ROLE_AWARE_TESTS=True

def test_viewer_text_search_intersects_os_filtered_registrations(tmp_path,monkeypatch):
 import db,auth,api
 from fastapi.testclient import TestClient
 path=tmp_path/'DEMO-os-filter.sqlite';monkeypatch.setenv('ASSET_DB_PATH',str(path));db.init_db(path);c=db.get_connection(path)
 db.create_user(c,'DEMO-os-viewer',auth.hash_password('DEMO-local-only-123'),role='viewer')
 for s,h,ip,os in [('DEMO-OS-1','DEMO-TARGET','10.99.8.1','Windows Server 2019'),('DEMO-OS-2','DEMO-OTHER','10.99.8.2','Windows Server 2019'),('DEMO-OS-3','DEMO-LINUX','10.99.8.3','Red Hat Enterprise Linux 9')]:db.insert_hardware(c,asset_serial=s,hostname=h,ip=ip,os=os,asset_status='使用中')
 c.commit();c.close();original=socket.socket.connect
 def guard(sock,address,*a,**kw):
  if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1'):return original(sock,address,*a,**kw)
  raise AssertionError('Unexpected external socket')
 monkeypatch.setattr(socket.socket,'connect',guard)
 def dep():
  con=db.get_connection(path)
  try:yield con
  finally:con.close()
 old=dict(api.app.dependency_overrides);api.app.dependency_overrides[api.get_db]=dep;client=TestClient(api.app)
 try:
  assert client.post('/api/auth/login',json={'username':'DEMO-os-viewer','password':'DEMO-local-only-123'}).status_code==200
  params={'os_source':'facts','os_class':'Windows','size':0}
  a=client.get('/api/v2/hosts',params=params);assert a.status_code==200,a.text
  assert {x['serial'] for x in a.json()['filter_rows']}=={'DEMO-OS-1','DEMO-OS-2'}
  b=client.get('/api/v2/hosts',params={**params,'q':'DEMO-TARGET'});assert b.status_code==200,b.text
  data=b.json();got=[x['serial'] for x in data['filter_rows']]
  assert got==['DEMO-OS-1'],f'OS filtered visible rows ignored q: {got}'
  assert data['filter_total']==1
 finally:client.close();api.app.dependency_overrides.clear();api.app.dependency_overrides.update(old)
