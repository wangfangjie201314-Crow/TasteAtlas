import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from backend.guided import GuidedService,GuidedError
from backend.qloo_client import QlooClient,QlooError
from backend.mcp_server import Server
from backend import main


class GuidedAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.database=Path(self.temp.name)/'guided.sqlite'
        self.requests=[];self.clients=[];self.now=1000
        self.service=GuidedService(self.database,self.factory,24,lambda:self.now)

    def tearDown(self):self.temp.cleanup()

    def handler(self,req):
        self.requests.append(req)
        params=dict(req.url.params)
        if req.url.path=='/search':
            return httpx.Response(200,json={'results':[{'entity_id':'seed','name':'Radiohead','types':['urn:entity:artist'],'disambiguation':'Radiohead band'}]})
        if req.url.path=='/v2/tags':
            category=params['filter.query']
            self.assertEqual(params['filter.tag.types'],'urn:tag:genre:place')
            return httpx.Response(200,json={'results':{'tags':[{'id':'urn:tag:genre:place:'+category,'name':category.title()}]}})
        self.assertEqual(params['signal.interests.entities'],'seed')
        kind=params['filter.type']
        if kind=='urn:entity:place':
            self.assertEqual(params['filter.location.query'],'Austin')
            self.assertEqual(params['filter.location.radius'],'0')
        else:self.assertNotIn('filter.location.query',params)
        identity=params.get('filter.tags','film')
        return httpx.Response(200,json={'results':{'entities':[{'entity_id':identity,'name':identity,'subtype':kind,
                 'properties':{'description':'Qloo candidate','geocode':{'city':'Austin'}}}]}})

    def factory(self,reserve):
        client=QlooClient('offline-fixture',transport=httpx.MockTransport(self.handler),before_request=reserve)
        self.clients.append(client);return client

    def resolved(self):return self.service.resolve('Austin','Radiohead')

    def test_full_confirmation_plan_cache_and_restart(self):
        profile=self.resolved()
        self.assertEqual(profile['state'],'ready')
        self.assertEqual(profile['matches'][0]['kind'],'urn:entity:artist')
        self.assertEqual(profile['matches'][0]['description'],'Radiohead band')
        result=self.service.plan(profile['profile_ref'],'seed')
        self.assertEqual(len(result['stops']),3)
        self.assertEqual(result['llm_calls'],0)
        self.assertEqual(result['usage']['used'],6)
        self.assertEqual(result['qloo_requests_to_build'],5)
        before=len(self.requests)
        restarted=GuidedService(self.database,self.factory,24,lambda:self.now)
        self.assertTrue(restarted.plan(profile['profile_ref'],'seed')['from_cache'])
        self.assertEqual(len(self.requests),before)
        self.assertEqual(restarted.state(profile['profile_ref'])['result']['stops'],result['stops'])
        self.assertTrue(all(c._client.is_closed for c in self.clients))

    def test_unconfirmed_id_and_expiry_do_not_call_qloo(self):
        profile=self.resolved();ref=profile['profile_ref']
        with self.assertRaises(GuidedError) as caught:self.service.plan(ref,'another-id')
        self.assertEqual(caught.exception.status,422)
        self.now+=901
        with self.assertRaises(GuidedError) as caught:self.service.plan(ref,'seed')
        self.assertEqual(caught.exception.status,410)
        self.assertEqual(len(self.requests),1)

    def test_budget_exhaustion_precedes_network_and_failed_plan_cannot_retry(self):
        path=Path(self.temp.name)/'one.sqlite'
        service=GuidedService(path,self.factory,1)
        profile=service.resolve('Austin','Radiohead')
        with self.assertRaises(GuidedError) as caught:service.plan(profile['profile_ref'],'seed')
        self.assertEqual(caught.exception.status,429)
        with self.assertRaises(GuidedError):service.plan(profile['profile_ref'],'seed')
        self.assertEqual(len(self.requests),1)
        self.assertEqual(service.usage()['used'],1)
        self.assertTrue(all(c._client.is_closed for c in self.clients))

    def test_parallel_budget_reservations_share_persistent_cap(self):
        path=Path(self.temp.name)/'parallel.sqlite'
        left=GuidedService(path,self.factory,3);right=GuidedService(path,self.factory,3)
        def reserve(i):
            try:(left if i%2 else right).reserve('/search');return True
            except GuidedError:return False
        with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(reserve,range(12)))
        self.assertEqual(sum(results),3)
        self.assertEqual(left.usage(),{'used':3,'limit':3})
        with self.assertRaisesRegex(ValueError,'refusing to reset'):GuidedService(path,self.factory,4)

    def test_partial_and_total_upstream_failures_never_fabricate(self):
        profile=self.resolved()
        def fail(req):return httpx.Response(429,text='PRIVATE_UPSTREAM_BODY')
        def factory(reserve):
            client=QlooClient('offline',transport=httpx.MockTransport(fail),before_request=reserve)
            self.clients.append(client);return client
        self.service.factory=factory
        with self.assertRaises(QlooError) as caught:self.service.plan(profile['profile_ref'],'seed')
        self.assertNotIn('PRIVATE_UPSTREAM_BODY',str(caught.exception))
        self.assertEqual(self.service.state(profile['profile_ref'])['state'],'failed')
        self.assertTrue(all(c._client.is_closed for c in self.clients))

    def test_actual_http_user_flow_and_validation(self):
        with patch.object(main,'guided_service',return_value=self.service):
            with TestClient(main.app) as client:
                self.assertEqual(client.post('/api/tools/resolve',json={'city':'  ','favorite':'Radiohead'}).status_code,422)
                profile=client.post('/api/tools/resolve',json={'city':'Austin','favorite':'Radiohead'}).json()
                selected={'profile_ref':profile['profile_ref'],'entity_id':'seed'}
                self.assertEqual(client.post('/api/tools/plan',json=selected).status_code,200)
                self.assertEqual(client.post('/api/tools/state',json={'profile_ref':profile['profile_ref']}).json()['state'],'complete')
        self.assertEqual(len(self.requests),6)

    def test_mcp_discovery_confirmation_and_cached_state(self):
        server=Server(lambda:self.service)
        def send(ident,method,params=None):return server.handle({'jsonrpc':'2.0','id':ident,'method':method,'params':params or {}})
        self.assertIn('error',send(0,'tools/list'))
        self.assertEqual(send(1,'initialize',{'protocolVersion':'2025-06-18'})['result']['protocolVersion'],'2025-06-18')
        self.assertIsNone(server.handle({'jsonrpc':'2.0','method':'notifications/initialized'}))
        self.assertEqual(len(send(2,'tools/list')['result']['tools']),3)
        for name in [[],{},None,17]:
            self.assertIn('error',send(21,'tools/call',{'name':name,'arguments':{}}))
        self.assertIn('result',send(22,'tools/list'))
        self.assertIn('error',send(3,'tools/call',{'name':'tasteatlas_resolve','arguments':{'city':'   ','favorite':'Radiohead'}}))
        resolved=send(4,'tools/call',{'name':'tasteatlas_resolve','arguments':{'city':'Austin','favorite':'Radiohead'}})['result']['structuredContent']
        plan=send(5,'tools/call',{'name':'tasteatlas_plan','arguments':{'profile_ref':resolved['profile_ref'],'entity_id':'seed'}})
        self.assertFalse(plan['result']['isError'])
        self.assertEqual(plan['result']['structuredContent']['llm_calls'],0)
        self.assertEqual(len(self.requests),6)

    def test_interrupted_planning_expires_without_reissuing_qloo(self):
        profile=self.resolved();ref=profile['profile_ref']
        with self.service._connect() as conn:
            payload=json.loads(conn.execute('SELECT payload FROM profiles WHERE ref=?',(ref,)).fetchone()[0])
            payload['started']=self.now
            conn.execute("UPDATE profiles SET state='planning',payload=? WHERE ref=?",(json.dumps(payload),ref))
        self.assertEqual(self.service.state(ref)['state'],'planning')
        self.now+=181
        self.assertEqual(self.service.state(ref)['state'],'failed')
        self.assertEqual(len(self.requests),1)

    def test_workflow_deadline_stops_before_reservation(self):
        client=self.factory(self.service.reserve)
        client._deadline=0
        with self.assertRaisesRegex(QlooError,'timed out'):client.search_entities('Radiohead')
        self.assertEqual(self.service.usage()['used'],0)
        self.assertEqual(len(self.requests),0)
        client.close()


if __name__=='__main__':unittest.main()
