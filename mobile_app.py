"""Public installation shell; all dashboard data keeps its existing login protection."""
import base64
import json
from functools import lru_cache
from starlette.responses import Response
from starlette.routing import Route
from mobile_icons import ICON_192, ICON_512

MANIFEST = {
    'id':'/mobile/', 'name':'Al Bonian FM Operations', 'short_name':'ABFM Operations',
    'description':'Al Bonian FM operations dashboard for authorized staff.',
    'start_url':'/', 'scope':'/', 'display':'standalone', 'orientation':'any',
    'background_color':'#F7F5F0', 'theme_color':'#122843', 'lang':'en',
    'icons':[
        {'src':'/mobile/icon-192.png','sizes':'192x192','type':'image/png','purpose':'any'},
        {'src':'/mobile/icon-512.png','sizes':'512x512','type':'image/png','purpose':'any maskable'},
    ],
}
# No fetch handler and no Cache Storage: work orders, sessions and exports stay online.
WORKER = """self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
"""
SCRIPT = """'use strict';
let installPrompt=null;
const button=document.querySelector('#install');
const status=document.querySelector('#status');
const ios=/iPad|iPhone|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
const standalone=window.matchMedia('(display-mode: standalone)').matches||navigator.standalone===true;
if(ios){document.querySelector('#ios').classList.add('recommended');button.textContent='Show iPhone / iPad steps';}
if(standalone){button.hidden=true;status.textContent='The app is already open in app mode. Tap Open dashboard to sign in.';}
window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();installPrompt=event;if(!standalone)button.textContent='Install mobile app';});
window.addEventListener('appinstalled',()=>{installPrompt=null;button.hidden=true;status.textContent='App installed. Open ABFM Operations from your home screen.';});
button.addEventListener('click',async()=>{
 if(installPrompt){const prompt=installPrompt;installPrompt=null;try{await prompt.prompt();const choice=await prompt.userChoice;status.textContent=choice.outcome==='accepted'?'Installation requested. Follow your browser instructions.':'Installation cancelled. You can try again from the browser menu.';}catch(_){status.textContent='Use your browser menu and choose Install app or Add to Home Screen.';}button.textContent='Show installation steps';}
 else{const steps=document.querySelector(ios?'#ios':'#android');steps.classList.add('recommended');steps.scrollIntoView({behavior:'smooth',block:'center'});status.textContent=ios?'In Safari, tap Share, then Add to Home Screen.':'In Chrome or Edge, open the browser menu and choose Install app or Add to Home screen. If this is an in-app browser, open this link in Chrome or Safari first.';}
});
document.querySelector('#copy').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(location.origin+'/mobile/');status.textContent='Installation link copied. Open it on your mobile.';}catch(_){status.textContent='Copy this address: '+location.origin+'/mobile/';}});
if('serviceWorker' in navigator&&window.isSecureContext){navigator.serviceWorker.register('/mobile-service-worker.js',{scope:'/'}).catch(()=>{status.textContent='You can still use the browser menu to add the app to your home screen.';});}
"""
PAGE = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Install ABFM Operations</title><meta name="theme-color" content="#122843"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="default"><meta name="apple-mobile-web-app-title" content="ABFM Operations"><link rel="manifest" href="/mobile/manifest.webmanifest"><link rel="apple-touch-icon" href="/mobile/icon-192.png"><link rel="icon" href="/mobile/icon-192.png">
<style>*{box-sizing:border-box}body{margin:0;background:#f7f5f0;color:#122843;font-family:Calibri,Arial,sans-serif;line-height:1.6}main{max-width:760px;margin:auto;padding:32px 20px 48px}header{background:#122843;color:#fffefa;border-bottom:4px solid #b3955b;padding:30px 24px;border-radius:18px}header img{width:86px;height:86px;float:right;margin-left:15px}h1{font-size:clamp(25px,5vw,36px);line-height:1.15;margin:10px 0}h2{font-size:20px;margin:0 0 8px}.eyebrow{color:#d6c49d;font-size:12px;letter-spacing:.14em}header p{color:#dce2eb;margin-bottom:0}.actions{display:flex;flex-wrap:wrap;gap:10px;margin:22px 0}button,.button{font:700 16px Calibri,Arial,sans-serif;display:inline-block;padding:13px 18px;min-height:48px;border:1px solid #b3955b;border-radius:9px;cursor:pointer;text-decoration:none;background:#122843;color:white}button.secondary,.button.secondary{background:#fffefa;color:#122843}button:focus-visible,a:focus-visible{outline:3px solid #b3955b;outline-offset:3px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.card{background:#fffefa;border:1px solid #ddd5c5;border-radius:14px;padding:22px}.recommended{border:2px solid #b3955b}ol{padding-left:22px;margin-bottom:0}#status{font-weight:600;min-height:26px}.note{font-size:14px;color:#596b80;margin-top:24px}footer{margin-top:24px;font-size:13px;color:#65758a}@media(max-width:540px){main{padding:16px 14px}.grid{grid-template-columns:1fr}header{padding:24px 18px}header img{width:66px;height:66px}.actions>*{width:100%;text-align:center}}</style>
<script src="/mobile/install.js" defer></script></head><body><main>
<header><img src="/mobile/icon-192.png" alt="Al Bonian FM"><div class="eyebrow">AL BONIAN FM · OPERATION WORKSPACE</div><h1>Operations, on your mobile.</h1><p>Install ABFM Operations on Android, iPhone, iPad or Windows. Open the live dashboard from your home screen.</p></header>
<div class="actions"><button id="install">Install mobile app</button><a class="button secondary" href="/">Open dashboard</a><button class="secondary" id="copy">Copy installation link</button></div><p id="status" role="status" aria-live="polite"></p>
<div class="grid"><section class="card" id="android"><h2>Android</h2><ol><li>Open this page in <strong>Chrome or Edge</strong>.</li><li>Tap <strong>Install mobile app</strong>. If no prompt appears, open the browser menu and choose <strong>Install app</strong> or <strong>Add to Home screen</strong>.</li><li>Open <strong>ABFM Operations</strong> and sign in with your existing account.</li></ol></section>
<section class="card" id="ios"><h2>iPhone / iPad</h2><ol><li>Open this page in <strong>Safari</strong>.</li><li>Tap <strong>Share</strong>, then <strong>Add to Home Screen</strong>. Enable <strong>Open as Web App</strong> if offered, and tap <strong>Add</strong>.</li><li>Open <strong>ABFM Operations</strong> and sign in with your existing account.</li></ol></section><section class="card" id="windows"><h2>Windows</h2><ol><li>Open this page in <strong>Microsoft Edge or Google Chrome</strong>.</li><li>Click <strong>Install mobile app</strong>, or use the browser menu: <strong>Apps &gt; Install this site as an app</strong> in Edge, or <strong>Install page as app</strong> in Chrome when available.</li><li>Open <strong>ABFM Operations</strong> from the Start menu and sign in with your existing account.</li></ol></section></div>
<p class="note">Use your existing admin or operations username and password. Installation does not bypass sign-in or change your permissions. The live dashboard requires an internet connection. Installation options depend on your browser; if unavailable, use Open dashboard. Windows users can install directly using the steps above. To install on a phone, use Copy installation link and open the link on your mobile.</p><footer>Al Bonian FM · Secure operations reporting</footer></main></body></html>'''

@lru_cache(maxsize=2)
def icon(size):
    return base64.b64decode(ICON_192 if size==192 else ICON_512)

async def mobile_endpoint(request):
    path=request.url.path
    assets={
        '/mobile':(PAGE,'text/html'), '/mobile/':(PAGE,'text/html'),
        '/mobile/manifest.webmanifest':(json.dumps(MANIFEST),'application/manifest+json'),
        '/mobile/install.js':(SCRIPT,'application/javascript'),
        '/mobile-service-worker.js':(WORKER,'application/javascript'),
        '/mobile/icon-192.png':(icon(192),'image/png'), '/mobile/icon-512.png':(icon(512),'image/png'),
    }
    content,kind=assets[path]
    headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin'}
    if kind=='text/html':
        headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; manifest-src 'self'; worker-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    if path=='/mobile-service-worker.js':headers['Service-Worker-Allowed']='/'
    return Response(content,media_type=kind,headers=headers)

def mobile_routes():
    return [Route(path,mobile_endpoint,methods=['GET']) for path in ['/mobile','/mobile/','/mobile/manifest.webmanifest','/mobile/install.js','/mobile-service-worker.js','/mobile/icon-192.png','/mobile/icon-512.png']]

def enable_mobile_app():
    """Add public install assets before Streamlit's SPA fallback; retain its middleware."""
    from streamlit.web.server.starlette import starlette_server
    original=starlette_server.create_starlette_app
    if getattr(original,'_abfm_mobile',False):return
    def create_app(runtime):
        app=original(runtime)
        app.router.routes[0:0]=mobile_routes()
        return app
    create_app._abfm_mobile=True
    starlette_server.create_starlette_app=create_app
