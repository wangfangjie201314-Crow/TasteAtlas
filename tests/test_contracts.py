"""Offline contract acceptance. These do not prove live integration."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.agents import Candidate, Critic, Profiler, Scout, TasteProfile
from backend.llm_client import LLMClient, LLMError
from backend.qloo_client import QlooClient, QlooError
from backend import main
from fastapi.testclient import TestClient


class ContractAcceptance(unittest.TestCase):
    def test_unknown_entity_does_not_skip_valid_keywords(self):
        class L:
            def chat_json(self, *a, **k):
                return {'entities':[{'name':'Unlisted film','kind':'movie'}], 'keywords':['jazz']}
        def respond(req):
            if req.url.path == '/search':
                return httpx.Response(404)
            return httpx.Response(200, json={'results':{'tags':[{'name':'jazz','tag_id':'urn:tag:genre:music:jazz'}]}})
        q = QlooClient('offline', transport=httpx.MockTransport(respond))
        try:
            profile, calls = Profiler(q, L()).run('Unlisted film and jazz', 'Austin', 'foodie_day')
            self.assertEqual(profile.signal_entity_ids, [])
            self.assertEqual(profile.signal_tag_ids, ['urn:tag:genre:music:jazz'])
            self.assertEqual(len(calls), 2)
        finally:
            q.close()

    def test_tag_query_and_geographic_filter(self):
        calls = []
        def respond(req):
            calls.append((req.url.path, dict(req.url.params)))
            return httpx.Response(200, json={'results': {'tags': [], 'entities': []}})
        q = QlooClient('offline', transport=httpx.MockTransport(respond))
        try:
            q.search_tags('jazz', types='urn:tag:genre:music')
            q.insights('urn:entity:place', signal_location_query='Austin')
            q.insights('urn:entity:movie', signal_location_query='Austin')
        finally:
            q.close()
        self.assertEqual(calls[0][1], {'filter.query':'jazz','filter.tag.types':'urn:tag:genre:music','take':'10'})
        self.assertEqual(calls[1][1]['filter.location.query'], 'Austin')
        self.assertEqual(calls[1][1]['filter.location.radius'], '0')
        self.assertNotIn('filter.location.query', calls[2][1])

    def test_search_list_response(self):
        q = QlooClient('offline', transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'results':[{'entity_id':'one'}]})))
        try:
            self.assertEqual(q.search_entities('example')[0]['entity_id'], 'one')
        finally:
            q.close()

    def test_slot_time_duplicate_and_tip_constraints(self):
        c = Candidate('lunch', 'Restaurant', 'one', 'urn:entity:place')
        planned = [None, {'slot':'dinner','entity_id':'one','time':'19:00'},
                   {'slot':'lunch','entity_id':'one','time':'25:99'},
                   {'slot':'lunch','entity_id':'one','time':'12:30','tip':'Open until midnight'},
                   {'slot':'lunch','entity_id':'one','time':'13:00'}]
        stops, dropped = Critic().run(planned, {'lunch':[c]})
        self.assertEqual(len(stops), 1)
        self.assertEqual(len(dropped), 4)
        self.assertNotIn('midnight', stops[0].tip)

    def test_invalid_inputs_never_build_pipeline(self):
        with patch.object(main, '_build_pipeline') as build:
            with TestClient(main.app) as tc:
                for payload in [{'city':'   ', 'likes':'ramen'}, {'city':'Austin','likes':'   '},
                                {'city':'Austin','likes':'ramen','occasion':'unknown'}]:
                    self.assertEqual(tc.post('/api/plan', json=payload).status_code, 422)
            build.assert_not_called()

    def test_upstream_body_not_exposed(self):
        q = QlooClient('offline', transport=httpx.MockTransport(lambda r: httpx.Response(503, text='PRIVATE_RESPONSE_MARKER')))
        try:
            with self.assertRaises(QlooError) as caught:
                q.search_entities('jazz')
            self.assertNotIn('PRIVATE_RESPONSE_MARKER', str(caught.exception))
        finally:
            q.close()

    def test_non_object_model_result_rejected(self):
        l = LLMClient('offline', transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'choices':[{'message':{'content':'[]'}}]})))
        try:
            with self.assertRaises(LLMError):
                l.chat_json('system', 'user')
        finally:
            l.close()

    def test_no_signals_and_lookup_failures_are_distinct(self):
        class L:
            def chat_json(self, *a, **k): return {'entities':[], 'keywords':[]}
        with self.assertRaisesRegex(RuntimeError, 'No taste matches'):
            Profiler(None, L()).run('ramen', 'Austin', 'foodie_day')
        class Q:
            def search_tags(self, *a, **k): raise QlooError('HTTP 429')
        with self.assertRaisesRegex(QlooError, '429'):
            Scout(Q()).run(TasteProfile('Austin', 'foodie_day'))

    def test_clients_closed_after_request_failure(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        q, l = Mock(), Mock()
        pipeline = SimpleNamespace(profiler=SimpleNamespace(qloo=q, llm=l), plan=Mock(side_effect=RuntimeError('No candidates')))
        with patch.object(main, '_build_pipeline', return_value=pipeline):
            with TestClient(main.app) as tc:
                self.assertEqual(tc.post('/api/plan', json={'city':'Austin','likes':'ramen'}).status_code, 400)
        q.close.assert_called_once()
        l.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
