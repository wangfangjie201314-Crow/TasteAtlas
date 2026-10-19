"""Explicit taste resolution -> confirmation -> grounded agent tool. No LLM."""
import json
from pathlib import Path
import secrets
import sqlite3
import time
from .agents import Candidate, Critic
from .qloo_client import ENTITY_PLACE, ENTITY_MOVIE


class GuidedError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status=status


class GuidedService:
    def __init__(self, database, client_factory, cap=24, clock=time.time, *, require_existing=False):
        if not 1 <= cap <= 1000:
            raise ValueError('invalid Qloo request cap')
        self.path=Path(database)
        self.require_existing=require_existing
        if not require_existing:
            self.path.parent.mkdir(parents=True,exist_ok=True)
        self.factory=client_factory
        self.clock=clock
        with self._connect() as conn:
            if not require_existing:
                conn.executescript('''CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY, cap INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, started REAL NOT NULL, path TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS profiles (ref TEXT PRIMARY KEY, expires REAL NOT NULL,
                    state TEXT NOT NULL, payload TEXT NOT NULL);''')
                conn.execute('INSERT OR IGNORE INTO budget VALUES (1,?)',(cap,))
            if conn.execute('SELECT cap FROM budget WHERE id=1').fetchone()[0] != cap:
                raise ValueError('Existing budget differs; refusing to reset it.')

    def _connect(self):
        from contextlib import contextmanager
        @contextmanager
        def connection():
            conn=(sqlite3.connect(self.path.resolve().as_uri()+'?mode=rw',uri=True,timeout=10)
                  if self.require_existing else sqlite3.connect(self.path,timeout=10))
            conn.row_factory=sqlite3.Row
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()
        return connection()

    def reserve(self, path):
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            count=conn.execute('SELECT COUNT(*) FROM calls').fetchone()[0]
            cap=conn.execute('SELECT cap FROM budget WHERE id=1').fetchone()[0]
            if count>=cap:
                raise GuidedError(429,'The local Qloo request budget is exhausted. No request was sent.')
            conn.execute('INSERT INTO calls(started,path) VALUES (?,?)',(self.clock(),path))

    def usage(self):
        with self._connect() as conn:
            return {'used':conn.execute('SELECT COUNT(*) FROM calls').fetchone()[0],
                    'limit':conn.execute('SELECT cap FROM budget WHERE id=1').fetchone()[0]}

    def resolve(self,city,favorite):
        city,favorite=city.strip(),favorite.strip()
        if not 2<=len(city)<=80 or not 2<=len(favorite)<=120:
            raise GuidedError(422,'Enter a city and a named artist, film, book or brand.')
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM profiles WHERE expires < ?',(self.clock(),))
            if conn.execute('SELECT COUNT(*) FROM profiles').fetchone()[0]>=32:
                raise GuidedError(429,'Too many active plans. Please wait for a plan to expire.')
        client=self.factory(self.reserve)
        try:
            hits=client.search_entities(favorite,take=3)
        finally:
            client.close()
        matches=[{'entity_id':e['entity_id'],'name':str(e.get('name') or 'Unnamed'),
                  'kind':str(e.get('subtype') or next((t for t in e.get('types',[]) if isinstance(t,str)),None) or e.get('type') or ''),
                  'description':str((e.get('properties') or {}).get('description') or e.get('disambiguation') or '')[:300]}
                 for e in hits if e.get('entity_id')]
        if not matches:
            raise GuidedError(404,'No matching cultural entity was found. Try a specific title or artist.')
        ref=secrets.token_urlsafe(24)
        payload={'city':city,'favorite':favorite,'matches':matches,'selected_id':None,'result':None}
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            # Check again after the network call to maintain the concurrent bound.
            if conn.execute('SELECT COUNT(*) FROM profiles WHERE expires >= ?',(self.clock(),)).fetchone()[0]>=32:
                raise GuidedError(429,'Too many active plans. Please wait for a plan to expire.')
            conn.execute('INSERT INTO profiles VALUES (?,?,?,?)',
                         (ref,self.clock()+900,'ready',json.dumps(payload)))
        return self.state(ref)

    def _row(self,conn,ref):
        row=conn.execute('SELECT * FROM profiles WHERE ref=?',(ref,)).fetchone()
        if row is None or row['expires']<self.clock():
            raise GuidedError(410,'This plan has expired. Your draft is kept; resolve the favorite again.')
        return row

    def state(self,ref):
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=self._row(conn,ref)
            payload=json.loads(row['payload'])
            state=row['state']
            if state=='planning' and self.clock()-payload.get('started',self.clock())>180:
                state='failed'
                conn.execute("UPDATE profiles SET state='failed' WHERE ref=?",(ref,))
        return {'profile_ref':ref,'state':state,**payload,'usage':self.usage()}

    def plan(self,ref,selected):
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=self._row(conn,ref)
            payload=json.loads(row['payload'])
            match=next((m for m in payload['matches'] if m['entity_id']==selected),None)
            if match is None:
                raise GuidedError(422,'Confirm one of the returned matches first.')
            if row['state']=='complete':
                if payload['selected_id']!=selected:
                    raise GuidedError(409,'This plan used a different match. Resolve again to change it.')
                return {**payload['result'],'from_cache':True,'usage':self.usage()}
            if row['state']!='ready':
                raise GuidedError(409,'This plan is already running or failed. Resolve again; no automatic retry was sent.')
            payload['selected_id']=selected
            payload['started']=self.clock()
            conn.execute("UPDATE profiles SET state='planning',payload=? WHERE ref=?",(json.dumps(payload),ref))
        client=None
        try:
            client=self.factory(self.reserve)
            pools={}; calls=[]; warnings=[]
            specs=[('lunch',ENTITY_PLACE,'restaurant','12:30'),
                   ('explore',ENTITY_PLACE,'museum','14:30'),
                   ('evening_watch',ENTITY_MOVIE,None,'21:30')]
            planned=[];seen=set()
            for slot,kind,category,hour in specs:
                filters=None
                if category:
                    tags=client.search_tags(category,types='urn:tag:genre:place',take=10)
                    calls.append('GET /v2/tags category='+category)
                    found=next((t.get('id') or t.get('tag_id') for t in tags
                        if str(t.get('name','')).casefold() in {category,category+'s'}
                        and (t.get('id') or t.get('tag_id'))),None)
                    if found is None:
                        pools[slot]=[];warnings.append('No verified '+category+' category was resolved.');continue
                    filters=[found]
                entities=client.insights(filter_type=kind,signal_interests_entities=[selected],
                    signal_location_query=payload['city'],filter_tags=filters,take=4)
                calls.append('GET /v2/insights type='+kind+(' city-filter='+payload['city'] if kind==ENTITY_PLACE else ' cultural-inspiration'))
                pools[slot]=[Candidate.from_qloo(slot,e) for e in entities if e.get('entity_id')
                             and (e.get('properties') or {}).get('is_closed') is not True]
                candidate=next((c for c in pools[slot] if c.entity_id not in seen),None)
                if candidate is None:
                    warnings.append('No usable candidate for '+slot+'.');continue
                seen.add(candidate.entity_id)
                planned.append({'slot':slot,'time':hour,'entity_id':candidate.entity_id,
                    'why':"Qloo ranked this candidate using your confirmed interest in "+match['name']+
                          (', with the selected city and category filters.' if kind==ENTITY_PLACE else '. This is cultural inspiration, not a local event.')})
            stops,dropped=Critic().run(planned,pools)
            if not any(s.kind==ENTITY_PLACE for s in stops):
                raise GuidedError(422,'No local places were returned. Try a broader city or another confirmed favorite.')
            result={'mode':'qloo_agent_tool','llm_calls':0,'city':payload['city'],'occasion':'guided_taste_day',
                'taste_profile':'Confirmed Qloo match: '+match['name'],'confirmed_match':match,
                'stops':[s.to_dict() for s in stops],'dropped_by_critic':dropped,'qloo_calls':calls,
                'warnings':warnings,'partial':len(stops)<3,'qloo_requests_to_build':client.request_count,
                'from_cache':False,'usage':self.usage()}
            payload['result']=result
            with self._connect() as conn:
                conn.execute("UPDATE profiles SET state='complete',payload=? WHERE ref=?",(json.dumps(payload),ref))
            return result
        except Exception:
            with self._connect() as conn:
                conn.execute("UPDATE profiles SET state='failed' WHERE ref=?",(ref,))
            raise
        finally:
            if client is not None:client.close()
