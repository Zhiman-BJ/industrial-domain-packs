import base64, json, os, time, urllib.parse, urllib.request, urllib.error
HOST = 'ccr.ccs.tencentyun.com'
BASE = 'https://' + HOST
REPO = 'industrial-mcp-remote/chip-rtl'
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
opener = urllib.request.build_opener(NoRedirect())
def request(label, method, url, data=None, headers=None):
    parsed = urllib.parse.urlsplit(url)
    assert parsed.scheme == 'https' and parsed.netloc == HOST
    started = time.monotonic()
    print(json.dumps({'phase':label,'state':'start','bytes':len(data or b'')}), flush=True)
    try:
        r = opener.open(urllib.request.Request(url,data=data,headers=headers or {},method=method),timeout=90)
    except urllib.error.HTTPError as e:
        r = e
    except Exception as e:
        print(json.dumps({'phase':label,'error':type(e).__name__,'seconds':round(time.monotonic()-started,2)}),flush=True)
        raise RuntimeError(label + ' failed') from None
    body=r.read()
    print(json.dumps({'phase':label,'status':r.status,'seconds':round(time.monotonic()-started,2),'responseBytes':len(body)}),flush=True)
    return r.status, r.headers, body
raw = (os.environ['TENCENT_REGISTRY_USERNAME']+':'+os.environ['TENCENT_REGISTRY_PASSWORD']).encode()
query=urllib.parse.urlencode({'service':'token-service','scope':'repository:'+REPO+':pull,push,delete'})
status,_,body=request('auth','GET',BASE+'/service/token?'+query,headers={'Authorization':'Basic '+base64.b64encode(raw).decode()})
assert status==200
obj=json.loads(body); token=obj.get('token') or obj['access_token']; auth={'Authorization':'Bearer '+token}
# Print only granted action names, never JWT credentials or identity claims.
try:
    part=token.split('.')[1]; claims=json.loads(base64.urlsafe_b64decode(part+'='*(-len(part)%4)))
    print(json.dumps({'grantedActions':[a.get('actions') for a in claims.get('access',[])]}),flush=True)
except Exception: pass
status,h,_=request('start-upload','POST',BASE+'/v2/'+REPO+'/blobs/uploads/',data=b'',headers=auth)
assert status==202
location=urllib.parse.urljoin(BASE,h['Location'])
try:
    for size in [1024,1024*1024,8*1024*1024]:
        status,h,_=request('patch-'+str(size),'PATCH',location,data=os.urandom(size),headers={**auth,'Content-Type':'application/octet-stream'})
        assert status==202
        location=urllib.parse.urljoin(BASE,h['Location'])
finally:
    status,_,_=request('cleanup-upload','DELETE',location,headers=auth)
    assert status==204
