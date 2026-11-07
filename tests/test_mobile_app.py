import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from streamlit.testing.v1 import AppTest
from security import connect,password_hash
from mobile_app import mobile_routes,enable_mobile_app

class MobileApplication(unittest.TestCase):
 def test_install_assets_manifest_and_private_data_cache_policy(self):
  async def dashboard(request):return PlainTextResponse('Dashboard authentication remains required')
  client=TestClient(Starlette(routes=mobile_routes()+[Route('/',dashboard)]))
  page=client.get('/mobile/');self.assertEqual(page.status_code,200)
  self.assertIn('Add to Home Screen',page.text);self.assertIn('<h2>Windows</h2>',page.text);self.assertIn('role="status"',page.text)
  self.assertIn("script-src 'self'",page.headers['content-security-policy'])
  manifest=client.get('/mobile/manifest.webmanifest').json()
  self.assertEqual(manifest['start_url'],'/');self.assertEqual(manifest['scope'],'/')
  self.assertEqual(manifest['display'],'standalone')
  for icon in manifest['icons']:
   response=client.get(icon['src']);self.assertEqual(response.headers['content-type'],'image/png')
   self.assertTrue(response.content.startswith(b'\x89PNG'))
   import struct
   width,height=struct.unpack('>II',response.content[16:24])
   self.assertEqual(f'{width}x{height}',icon['sizes'])
  worker=client.get('/mobile-service-worker.js')
  self.assertEqual(worker.headers['service-worker-allowed'],'/')
  self.assertEqual(worker.headers['cache-control'],'no-store')
  self.assertNotIn('fetch',worker.text);self.assertNotIn('caches.',worker.text)
  self.assertIn('beforeinstallprompt',client.get('/mobile/install.js').text)
  self.assertEqual(client.get('/').text,'Dashboard authentication remains required')
  self.assertEqual(client.post('/mobile/').status_code,405)
  self.assertEqual(client.get('/mobile/accounts.sqlite').status_code,404)

 def test_startup_route_hook_preserves_app_and_is_idempotent(self):
  from streamlit.web.server.starlette import starlette_server
  async def health(request):return PlainTextResponse('healthy')
  existing=Starlette(routes=[Route('/_stcore/health',health)])
  def original(runtime):return existing
  with patch.object(starlette_server,'create_starlette_app',new=original):
   enable_mobile_app();hook=starlette_server.create_starlette_app
   enable_mobile_app();self.assertIs(hook,starlette_server.create_starlette_app)
   app=hook(None);self.assertIs(app,existing)
   client=TestClient(app)
   self.assertEqual(client.get('/_stcore/health').text,'healthy')
   self.assertEqual(client.get('/mobile/').status_code,200)

 def test_install_link_appears_after_login_for_both_roles(self):
  with TemporaryDirectory() as directory:
   root=Path(directory);snapshot=root/'snapshot-mobile';snapshot.mkdir()
   import pandas as pd
   frame=pd.DataFrame({'Task Type':['BREAKDOWN'],'Status':['ACTIVE']})
   frame.to_parquet(snapshot/'tasks.parquet',index=False)
   manifest={'imported_at':'2026-11-07','pages':[{'name':'Reactive','visuals':[]}],'tables':{'Reactive':{'file':'tasks.parquet','columns':list(frame.columns),'rows':1}}}
   (snapshot/'manifest.json').write_text(json.dumps(manifest));(root/'current.txt').write_text(snapshot.name)
   db=connect(root)
   for role in ['admin','operations']:
    db.execute('INSERT INTO users (username,password,role) VALUES (?,?,?)',(role,password_hash('mobile-test-password'),role))
   db.commit();db.close()
   with patch.dict(os.environ,{'ABFM_DATA_DIR':directory}):
    for role in ['admin','operations']:
     app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=60).run()
     self.assertFalse(app.get('link_button'))
     app.text_input[0].set_value(role);app.text_input[1].set_value('mobile-test-password');app.button[0].click().run()
     self.assertFalse(app.exception)
     link=next(link for link in app.get('link_button') if link.proto.label=='Download / install mobile app')
     self.assertEqual(link.proto.url,'/mobile/')
     navigation=next(s for s in app.selectbox if s.label=='Go to page')
     self.assertEqual('Administration' in navigation.options,role=='admin')
     navigation.set_value('Reactive').run()
     self.assertFalse(app.exception)
     self.assertEqual(app.sidebar.radio[0].value,'Reactive')
     self.assertTrue(any('<h1>Reactive</h1>' in m.value for m in app.markdown))
     next(s for s in app.selectbox if s.label=='Go to page').set_value('Executive Summary').run()
     self.assertFalse(app.exception)
     self.assertEqual(app.sidebar.radio[0].value,'Executive Summary')
