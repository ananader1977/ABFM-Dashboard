"""Same-origin HTTPS login and durable, revocable browser sessions."""
import hmac
import html
import os
from pathlib import Path
import secrets
from urllib.parse import parse_qs,urlsplit
from starlette.requests import Request
from starlette.responses import HTMLResponse,RedirectResponse,PlainTextResponse
from starlette.routing import Route
from security import authenticate,browser_session_user,create_browser_session,SESSION_COOKIE,SESSION_LIFETIME

CSRF_COOKIE='__Host-abfm_login_csrf'
HEADERS={'Cache-Control':'no-store','Pragma':'no-cache','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin',
    'Content-Security-Policy':"default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'"}


def root_directory():
    return Path(os.environ.get('ABFM_DATA_DIR',Path(__file__).parent/'data'))


def login_response(error='',status=200):
    csrf=secrets.token_urlsafe(32)
    markup='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#122843"><title>Al Bonian FM | Sign in</title><link rel="icon" href="/mobile/icon-192.png"><style>*{box-sizing:border-box}body{margin:0;background:#f7f5f0;color:#122843;font:17px Calibri,Arial,sans-serif;line-height:1.5}main{max-width:430px;margin:7vh auto;padding:24px}header{background:#122843;border-top:3px solid #b3955b;border-radius:14px;text-align:center;padding:22px}header img{width:130px;height:130px}h1{font-size:30px;font-weight:500;margin:24px 0 4px}p{color:#61718a}label{display:block;font-weight:600;margin:16px 0 6px}input{width:100%;min-height:48px;padding:12px;font:inherit;border:1px solid #c9c3b6;border-radius:8px;background:#fffefa}button{width:100%;padding:13px;border:0;border-radius:8px;background:#122843;color:#fffefa;font:700 17px Calibri,Arial,sans-serif;cursor:pointer;margin:22px 0 10px}a{color:#122843}input:focus,button:focus-visible{outline:3px solid #b3955b;outline-offset:2px}.error{color:#b42318;background:#fff3ef;border:1px solid #e9b6aa;padding:12px;border-radius:8px}.note{font-size:14px}@media(max-width:480px){main{margin:2vh auto;padding:22px}}</style></head><body><main><header><img src="/mobile/icon-192.png" alt="Al Bonian FM"></header><h1>Operations intelligence</h1><p>Al Bonian Facilities Management · Secure access</p>ERROR<form method="post" action="/auth/login"><input type="hidden" name="csrf" value="CSRF"><label for="username">Username</label><input id="username" name="username" type="text" maxlength="100" autocomplete="username" required autofocus><label for="password">Password</label><input id="password" name="password" type="password" maxlength="256" autocomplete="current-password" required><button type="submit">Sign in</button></form><p class="note">Your sign-in remains valid for eight hours, including after a connection interruption. Sign out when using a shared device.</p></main></body></html>'''
    markup=markup.replace('ERROR','<p role="alert" class="error">'+html.escape(error)+'</p>' if error else '').replace('CSRF',csrf)
    response=HTMLResponse(markup,status_code=status,headers=HEADERS)
    response.set_cookie(CSRF_COOKIE,csrf,max_age=600,secure=True,httponly=True,samesite='strict',path='/')
    return response


async def login_endpoint(request):
    if request.method=='GET':
        if browser_session_user(root_directory(),request.cookies.get(SESSION_COOKIE)):
            return RedirectResponse('/',status_code=303,headers={'Cache-Control':'no-store'})
        return login_response()
    origin=request.headers.get('origin','')
    if not origin or urlsplit(origin).netloc!=request.url.netloc or urlsplit(origin).scheme!='https':
        return PlainTextResponse('Sign-in request rejected. Open the sign-in page and try again.',status_code=403,headers=HEADERS)
    if request.headers.get('content-type','').split(';')[0]!='application/x-www-form-urlencoded':
        return PlainTextResponse('Invalid sign-in request.',status_code=415,headers=HEADERS)
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>4096:return PlainTextResponse('Invalid sign-in request.',status_code=413,headers=HEADERS)
    try:fields=parse_qs(body.decode('utf-8'),keep_blank_values=True,max_num_fields=5)
    except (UnicodeError,ValueError):return login_response('Invalid sign-in request.',400)
    if any(len(fields.get(key,[]))!=1 for key in ['csrf','username','password']):return login_response('Invalid sign-in request.',400)
    csrf=fields['csrf'][0];cookie=request.cookies.get(CSRF_COOKIE,'')
    if not csrf or not cookie or not hmac.compare_digest(csrf,cookie):
        return login_response('The sign-in form expired. Please try again.',403)
    username,password=fields['username'][0],fields['password'][0]
    if len(username)>100 or len(password)>256:
        return login_response('Invalid credentials or account temporarily locked.',401)
    user=authenticate(root_directory(),username,password)
    if not user:return login_response('Invalid credentials or account temporarily locked. After five failed attempts, wait 15 minutes.',401)
    try:token=create_browser_session(root_directory(),user)
    except ValueError:return login_response('Account changed. Please sign in again.',401)
    response=RedirectResponse('/',status_code=303,headers={'Cache-Control':'no-store'})
    response.set_cookie(SESSION_COOKIE,token,max_age=SESSION_LIFETIME,secure=True,httponly=True,samesite='lax',path='/')
    response.delete_cookie(CSRF_COOKIE,secure=True,httponly=True,samesite='strict',path='/')
    return response


class BrowserSessionGate:
    """Protect entry navigation without changing Streamlit's WebSocket handling."""
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']=='http' and scope['method']=='GET' and scope['path']=='/':
            request=Request(scope)
            if not browser_session_user(root_directory(),request.cookies.get(SESSION_COOKIE)):
                await login_response()(scope,receive,send)
                return
        await self.app(scope,receive,send)


def auth_routes():
    return [Route('/auth/login',login_endpoint,methods=['GET','POST'])]
