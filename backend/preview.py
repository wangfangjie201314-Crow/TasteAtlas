"""Opt-in, authenticated review surface. No tunnel or external I/O here."""
import asyncio
import base64
import binascii
from collections import deque
import hmac
import json
import re
import time
from urllib.parse import urlsplit


class PreviewGate:
    """One-process review gate; durable Qloo quotas remain in GuidedService."""
    READ_PATHS={'/','/api/health','/static/styles.css','/static/guided.js','/static/render.js'}
    TOOL_PATHS={'/api/tools/resolve','/api/tools/plan','/api/tools/state'}
    SECURITY=[(b'cache-control',b'no-store'),(b'referrer-policy',b'no-referrer'),
              (b'x-content-type-options',b'nosniff'),(b'x-frame-options',b'DENY')]

    def __init__(self,app,password,*,clock=time.monotonic,body_timeout=10):
        if not isinstance(password,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',password):
            raise ValueError('A private strong review access code is required.')
        if not isinstance(body_timeout,(int,float)) or not 0<body_timeout<=10:
            raise ValueError('Invalid request-body deadline.')
        self.app=app
        self.expected=('reviewer:'+password).encode('ascii')
        self.clock=clock
        self.body_timeout=body_timeout
        self.actions=deque()

    async def __call__(self,scope,receive,send):
        if scope['type']=='websocket':
            await send({'type':'websocket.close','code':1008});return
        if scope['type']!='http':
            await self.app(scope,receive,send);return

        async def secured_send(message):
            if message['type']=='http.response.start':
                message=dict(message)
                names={k for k,_ in self.SECURITY}
                message['headers']=[(k,v) for k,v in message.get('headers',[]) if k.lower() not in names]+self.SECURITY
            await send(message)

        async def reject(status,detail,extra=()):
            body=json.dumps({'detail':detail},separators=(',',':')).encode()
            await secured_send({'type':'http.response.start','status':status,
                'headers':[(b'content-type',b'application/json'),(b'content-length',str(len(body)).encode()),*extra]})
            await secured_send({'type':'http.response.body','body':body})

        def values(name):return [v for k,v in scope.get('headers',[]) if k.lower()==name]
        auth=values(b'authorization')
        authenticated=False
        if len(auth)==1 and len(auth[0])<=512:
            parts=auth[0].split(b' ',1)
            if len(parts)==2 and parts[0].lower()==b'basic':
                try:authenticated=hmac.compare_digest(base64.b64decode(parts[1],validate=True),self.expected)
                except (ValueError,binascii.Error):pass
        if not authenticated:
            await reject(401,'Review access required.',[(b'www-authenticate',b'Basic realm="TasteAtlas review", charset="UTF-8"')]);return
        method=scope.get('method','');path=scope.get('path','')
        if not ((method in {'GET','HEAD'} and path in self.READ_PATHS) or (method=='POST' and path in self.TOOL_PATHS)):
            await reject(404,'Not available in this review.');return
        body=b''
        if method=='POST':
            host=values(b'host');origin=values(b'origin');content_type=values(b'content-type');length=values(b'content-length')
            if len(host)!=1 or len(origin)>1:
                await reject(403,'Same-origin request required.');return
            if origin:
                try:
                    parsed=urlsplit(origin[0].decode('ascii'))
                    valid=parsed.scheme in {'http','https'} and parsed.netloc.encode('ascii')==host[0] and not parsed.path and not parsed.query and not parsed.fragment
                except (ValueError,UnicodeError):valid=False
                if not valid:
                    await reject(403,'Same-origin request required.');return
            if len(content_type)!=1 or content_type[0].split(b';',1)[0].strip().lower()!=b'application/json':
                await reject(415,'JSON requests only.');return
            if len(length)>1 or (length and (not length[0].isdigit() or len(length[0])>8)):
                await reject(400,'Invalid content length.');return
            if length and int(length[0])>8192:
                await reject(413,'Request body too large.');return
            async def read_body():
                chunks=[];size=0
                while True:
                    message=await receive()
                    if message['type']=='http.disconnect':return None
                    if message['type']!='http.request':continue
                    chunk=message.get('body',b'');size+=len(chunk)
                    if size>8192:return False
                    chunks.append(chunk)
                    if not message.get('more_body',False):return b''.join(chunks)
            try:body=await asyncio.wait_for(read_body(),timeout=self.body_timeout)
            except asyncio.TimeoutError:
                await reject(408,'Request body deadline exceeded.');return
            if body is None:return
            if body is False:
                await reject(413,'Request body too large.');return
            if path!='/api/tools/state':
                now=self.clock()
                while self.actions and self.actions[0]<=now-60:self.actions.popleft()
                if len(self.actions)>=4:
                    await reject(429,'Please wait before starting another review action.',[(b'retry-after',b'60')]);return
                self.actions.append(now)
        replayed=False
        async def replay_receive():
            nonlocal replayed
            if not replayed:
                replayed=True
                return {'type':'http.request','body':body,'more_body':False}
            return await receive()
        await self.app(scope,replay_receive if method=='POST' else receive,secured_send)
