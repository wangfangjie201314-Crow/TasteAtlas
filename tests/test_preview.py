import asyncio
import base64
import json
import unittest
from fastapi import FastAPI,Request
from fastapi.testclient import TestClient
from backend.preview import PreviewGate

PASSWORD='fixture-only-'*4


class PreviewAcceptance(unittest.TestCase):
    def setUp(self):
        self.now=100.0;self.calls=[]
        app=FastAPI()
        @app.api_route('/{path:path}',methods=['GET','HEAD','POST','OPTIONS'])
        async def echo(request:Request,path:str):
            self.calls.append(path)
            return {'path':path,'body':(await request.body()).decode('utf-8')}
        self.gate=PreviewGate(app,PASSWORD,clock=lambda:self.now)
        self.web=TestClient(self.gate)
        self.auth=('reviewer',PASSWORD)

    def test_missing_wrong_and_duplicate_auth_never_reach_app(self):
        for auth in [None,('reviewer','wrong'),('wrong',PASSWORD)]:
            response=self.web.get('/',auth=auth)
            self.assertEqual(response.status_code,401)
            self.assertIn('Basic',response.headers['www-authenticate'])
            self.assertNotIn(PASSWORD,response.text)
        header='Basic '+base64.b64encode(('reviewer:'+PASSWORD).encode()).decode()
        self.assertEqual(self.web.get('/',headers=[('Authorization',header),('Authorization',header)]).status_code,401)
        self.assertEqual(self.calls,[])

    def test_allowlist_hides_old_llm_schema_files_and_wrong_methods(self):
        for path in ['/api/plan','/docs','/openapi.json','/.env','/static/index.html','/static/../backend/config.py']:
            self.assertEqual(self.web.get(path,auth=self.auth).status_code,404)
        self.assertEqual(self.web.post('/api/plan',auth=self.auth,json={}).status_code,404)
        self.assertEqual(self.web.options('/api/tools/resolve',auth=self.auth).status_code,404)
        self.assertEqual(self.calls,[])
        for path in self.gate.READ_PATHS:
            response=self.web.get(path,auth=self.auth)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertEqual(response.headers['referrer-policy'],'no-referrer')

    def test_json_replayed_intact_with_matching_origin(self):
        response=self.web.post('/api/tools/resolve',auth=self.auth,json={'city':'Austin'},headers={'Origin':'http://testserver'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(json.loads(response.json()['body']),{'city':'Austin'})

    def test_cross_origin_and_wrong_media_type_do_not_reach_app(self):
        for origin in ['https://evil.invalid','null','http://testserver/path','http://testserver#fragment']:
            self.assertEqual(self.web.post('/api/tools/resolve',auth=self.auth,json={},headers={'Origin':origin}).status_code,403)
        self.assertEqual(self.web.post('/api/tools/resolve',auth=self.auth,content='{}').status_code,415)
        self.assertEqual(self.calls,[])

    def test_body_limits_cover_declared_and_streamed_payloads(self):
        self.assertEqual(self.web.post('/api/tools/resolve',auth=self.auth,content=b'x'*8193,headers={'Content-Type':'application/json'}).status_code,413)
        def stream():
            yield b'x'*4096;yield b'x'*4097
        self.assertEqual(self.web.post('/api/tools/resolve',auth=self.auth,content=stream(),headers={'Content-Type':'application/json'}).status_code,413)
        self.assertEqual(self.web.post('/api/tools/resolve',auth=self.auth,content=b'{}',headers={'Content-Type':'application/json','Content-Length':'-1'}).status_code,400)
        self.assertEqual(self.calls,[])

    def test_action_rate_bound_does_not_block_cache_polling(self):
        for _ in range(4):self.assertEqual(self.web.post('/api/tools/resolve',auth=self.auth,json={}).status_code,200)
        response=self.web.post('/api/tools/plan',auth=self.auth,json={})
        self.assertEqual(response.status_code,429)
        self.assertEqual(response.headers['retry-after'],'60')
        self.assertEqual(self.web.post('/api/tools/state',auth=self.auth,json={}).status_code,200)
        self.now+=60
        self.assertEqual(self.web.post('/api/tools/plan',auth=self.auth,json={}).status_code,200)

    def test_secret_configuration_is_explicit(self):
        for value in ['',None,'short','x'*129,' '*40]:
            with self.assertRaises(ValueError):PreviewGate(object(),value)

    def test_stream_deadline_precedes_inner_app(self):
        messages=[]
        async def never():await asyncio.sleep(1)
        async def send(message):messages.append(message)
        scope={'type':'http','method':'POST','path':'/api/tools/resolve','headers':[
            (b'authorization',b'Basic '+base64.b64encode(('reviewer:'+PASSWORD).encode())),
            (b'host',b'testserver'),(b'content-type',b'application/json')]}
        asyncio.run(PreviewGate(object(),PASSWORD,body_timeout=.01)(scope,never,send))
        self.assertEqual(messages[0]['status'],408)


if __name__=='__main__':unittest.main()
