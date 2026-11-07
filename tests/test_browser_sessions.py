import hashlib
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route,WebSocketRoute
from starlette.testclient import TestClient
from security import (connect,password_hash,authenticate,create_browser_session,browser_session_user,
    revoke_browser_session,configure_connection_secret,SESSION_COOKIE,SESSION_LIFETIME,save_user)
from browser_sessions import BrowserSessionGate,auth_routes,CSRF_COOKIE

class PersistentBrowserSessions(unittest.TestCase):
 def setUp(self):
  self.temp=TemporaryDirectory();self.root=Path(self.temp.name)
  with connect(self.root) as db:
   for role in ['admin','operations']:
    db.execute('INSERT INTO users (username,password,role) VALUES (?,?,?)',(role,password_hash('synthetic-password'),role))
  self.env=patch.dict(os.environ,{'ABFM_DATA_DIR':str(self.root)});self.env.start()
 def tearDown(self):self.env.stop();self.temp.cleanup()
 def app(self):
  async def dashboard(request):return PlainTextResponse('Authenticated dashboard')
  async def health(request):return PlainTextResponse('ok')
  async def websocket(socket):
   await socket.accept();await socket.send_text('connected');await socket.close()
  app=Starlette(routes=auth_routes()+[Route('/',dashboard),Route('/_stcore/health',health),WebSocketRoute('/_stcore/stream',websocket)])
  app.add_middleware(BrowserSessionGate);return app
 def form(self,client):
  response=client.get('/auth/login')
  self.assertEqual(response.status_code,200)
  return re.search(r'name="csrf" value="([^"]+)"',response.text).group(1)
 def signin(self,client,role='admin'):
  csrf=self.form(client)
  return client.post('/auth/login',data={'username':role,'password':'synthetic-password','csrf':csrf},headers={'Origin':'https://testserver'},follow_redirects=False)
 def test_login_refresh_reconnect_and_new_server_share_revocable_session(self):
  for role in ['admin','operations']:
   client=TestClient(self.app(),base_url='https://testserver')
   self.assertIn('Operations intelligence',client.get('/').text)
   response=self.signin(client,role)
   self.assertEqual(response.status_code,303)
   token=client.cookies.get(SESSION_COOKIE)
   self.assertEqual(browser_session_user(self.root,token)['role'],role)
   cookie=response.headers.get_list('set-cookie')[0].lower()
   self.assertTrue('httponly' in cookie and 'secure' in cookie and 'samesite=lax' in cookie)
   self.assertIn('max-age=28800',cookie)
   self.assertEqual(client.get('/').text,'Authenticated dashboard')
   restarted=TestClient(self.app(),base_url='https://testserver')
   restarted.cookies.set(SESSION_COOKIE,token,domain='testserver.local',path='/')
   self.assertEqual(restarted.get('/').text,'Authenticated dashboard')
   revoke_browser_session(self.root,token)
   self.assertIn('Operations intelligence',restarted.get('/').text)
   self.assertIsNone(browser_session_user(self.root,token))
 def test_token_is_hashed_expiry_and_password_change_invalidate_access(self):
  user=authenticate(self.root,'operations','synthetic-password');token=create_browser_session(self.root,user)
  with connect(self.root) as db:
   row=db.execute('SELECT token_hash,expires FROM browser_sessions').fetchone()
   self.assertEqual(row['token_hash'],hashlib.sha256(token.encode()).hexdigest())
   self.assertNotEqual(row['token_hash'],token)
   db.execute('UPDATE browser_sessions SET expires=0')
  self.assertIsNone(browser_session_user(self.root,token))
  token=create_browser_session(self.root,user)
  save_user(self.root,'admin','operations','operations','replacement-synthetic-password')
  self.assertIsNone(browser_session_user(self.root,token))
  self.assertIsNone(browser_session_user(self.root,'unrecognized-token'))
 def test_csrf_origin_and_bad_password_are_rejected_without_session(self):
  client=TestClient(self.app(),base_url='https://testserver');csrf=self.form(client)
  for origin,nonce in [('https://evil.example',csrf),('https://testserver','incorrect')]:
   response=client.post('/auth/login',data={'username':'admin','password':'synthetic-password','csrf':nonce},headers={'Origin':origin},follow_redirects=False)
   self.assertEqual(response.status_code,403);self.assertIsNone(client.cookies.get(SESSION_COOKIE))
  csrf=self.form(client)
  response=client.post('/auth/login',data={'username':'admin','password':'wrong','csrf':csrf},headers={'Origin':'https://testserver'},follow_redirects=False)
  self.assertEqual(response.status_code,401);self.assertIsNone(client.cookies.get(SESSION_COOKIE))
 def test_health_and_websocket_pass_through_without_login_redirect(self):
  client=TestClient(self.app(),base_url='https://testserver')
  self.assertEqual(client.get('/_stcore/health').text,'ok')
  with client.websocket_connect('/_stcore/stream') as websocket:self.assertEqual(websocket.receive_text(),'connected')
 def test_connection_signing_key_survives_restart_and_stays_private(self):
  with patch.dict(os.environ,{},clear=True):
   configure_connection_secret(self.root);first=os.environ.pop('STREAMLIT_SERVER_COOKIE_SECRET')
   configure_connection_secret(self.root);self.assertEqual(first,os.environ['STREAMLIT_SERVER_COOKIE_SECRET'])
   self.assertEqual((self.root/'streamlit_cookie_secret').stat().st_mode & 0o777,0o600)

 def test_dashboard_restores_from_cookie_and_signout_revokes_it(self):
  import json
  import pandas as pd
  import streamlit as st
  from streamlit.testing.v1 import AppTest
  from unittest.mock import PropertyMock
  snapshot=self.root/'snapshot-test';snapshot.mkdir()
  frame=pd.DataFrame({'Task Type':['BREAKDOWN'],'Status':['ACTIVE']})
  frame.to_parquet(snapshot/'tasks.parquet',index=False)
  (snapshot/'manifest.json').write_text(json.dumps({'imported_at':'2026-11-07','pages':[{'name':'Reactive','visuals':[]}],'tables':{'Reactive':{'file':'tasks.parquet','rows':1,'columns':list(frame.columns)}}}))
  (self.root/'current.txt').write_text(snapshot.name)
  user=authenticate(self.root,'operations','synthetic-password');token=create_browser_session(self.root,user)
  with patch.dict(os.environ,{'ABFM_BROWSER_SESSIONS':'1'}),patch.object(type(st.context),'cookies',new_callable=PropertyMock,return_value={SESSION_COOKIE:token}):
   for _ in range(2):
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=60).run()
    self.assertFalse(app.exception)
    self.assertEqual(app.sidebar.radio[0].value,'Executive Summary')
    self.assertNotIn('Administration',app.sidebar.radio[0].options)
   next(button for button in app.button if button.label=='Sign out').click().run()
   self.assertFalse(app.exception)
   self.assertIsNone(browser_session_user(self.root,token))
   self.assertTrue(any(link.proto.url=='/auth/login' for link in app.get('link_button')))

 def test_native_login_works_with_actual_streamlit_middleware_stack(self):
  from unittest.mock import MagicMock
  from streamlit.runtime import Runtime
  from streamlit.web.server.starlette import starlette_server
  from streamlit.web.server.starlette.starlette_app import create_starlette_app
  from mobile_app import enable_mobile_app
  with patch.dict(os.environ,{'ABFM_BROWSER_SESSIONS':'1'}),patch.object(starlette_server,'create_starlette_app',new=create_starlette_app):
   enable_mobile_app();app=starlette_server.create_starlette_app(MagicMock(spec=Runtime))
   client=TestClient(app,base_url='https://testserver')
   self.assertIn('Operations intelligence',client.get('/').text)
   self.assertEqual(client.get('/mobile/manifest.webmanifest').status_code,200)
   self.assertEqual(self.signin(client).status_code,303)
   response=client.get('/')
   self.assertEqual(response.status_code,200)
   self.assertIn('Streamlit',response.text)
