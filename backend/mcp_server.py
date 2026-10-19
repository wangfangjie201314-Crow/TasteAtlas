"""Small read-only stdio MCP server. No model/provider sign-in required here."""
import json
import sys
from pydantic import ValidationError
from .guided import GuidedError
from .qloo_client import QlooError
from .runtime import guided_service
from .tool_models import ResolveRequest,GuidedPlanRequest,ProfileRequest

TOOLS={
    'tasteatlas_resolve':(ResolveRequest,'Find up to three Qloo matches for a named cultural favorite in a city. Ask the user to confirm a returned match before planning.'),
    'tasteatlas_plan':(GuidedPlanRequest,'Build a city plan using the user-confirmed match. Only IDs returned for this profile are accepted; repeated completed calls use the private cache.'),
    'tasteatlas_state':(ProfileRequest,'Read a private, unexpired profile or cached plan without another Qloo request.'),
}


class Server:
    def __init__(self,service_factory=guided_service):
        self.initialized=False
        self.ready=False
        self.service_factory=service_factory

    def handle(self,message):
        if not isinstance(message,dict) or message.get('jsonrpc')!='2.0' or not isinstance(message.get('method'),str):
            return self.error(message.get('id') if isinstance(message,dict) else None,-32600,'Invalid request')
        method=message['method']
        if 'id' not in message:
            if method=='notifications/initialized' and self.initialized:self.ready=True
            return None
        ident=message['id']
        if ident is None or isinstance(ident,(dict,list,bool)):
            return self.error(None,-32600,'Invalid request identifier')
        if method=='initialize':
            if self.initialized:return self.error(ident,-32600,'Already initialized')
            self.initialized=True
            return {'jsonrpc':'2.0','id':ident,'result':{'protocolVersion':'2025-06-18',
                'capabilities':{'tools':{'listChanged':False}},'serverInfo':{'name':'TasteAtlas','version':'1.1.0'},
                'instructions':'Resolve a named favorite, confirm one returned match with the user, then plan. Cultural affinities are not personal probabilities or verified availability.'}}
        if method=='ping':return {'jsonrpc':'2.0','id':ident,'result':{}}
        if not self.ready:return self.error(ident,-32000,'Complete initialization before using tools')
        if method=='tools/list':
            return {'jsonrpc':'2.0','id':ident,'result':{'tools':[{'name':name,'description':description,
                'inputSchema':model.model_json_schema(),'annotations':{'readOnlyHint':True,'openWorldHint':True}}
                for name,(model,description) in TOOLS.items()]}}
        if method!='tools/call':return self.error(ident,-32601,'Method not found')
        params=message.get('params')
        if not isinstance(params,dict) or not isinstance(params.get('name'),str) or params['name'] not in TOOLS:
            return self.error(ident,-32602,'Unknown tool')
        name=params['name']
        try:
            data=TOOLS[name][0].model_validate(params.get('arguments',{}))
        except ValidationError:
            return self.error(ident,-32602,'Invalid tool arguments')
        try:
            service=self.service_factory()
            if name=='tasteatlas_resolve':result=service.resolve(data.city,data.favorite)
            elif name=='tasteatlas_plan':result=service.plan(data.profile_ref,data.entity_id)
            else:result=service.state(data.profile_ref)
            body={'content':[{'type':'text','text':json.dumps(result,ensure_ascii=False)}],
                  'structuredContent':result,'isError':False}
        except (GuidedError,QlooError) as error:
            body={'content':[{'type':'text','text':str(error)}],'isError':True}
        except Exception:
            body={'content':[{'type':'text','text':'The tool could not complete this request; no automatic retry was sent.'}],'isError':True}
        return {'jsonrpc':'2.0','id':ident,'result':body}

    @staticmethod
    def error(ident,code,message):
        return {'jsonrpc':'2.0','id':ident,'error':{'code':code,'message':message}}


def main():
    server=Server()
    while True:
        line=sys.stdin.buffer.readline(65537)
        if not line:break
        if len(line)>65536:
            # Drain only this oversized frame before accepting the next one.
            while line and not line.endswith(b'\n'):line=sys.stdin.buffer.readline(65537)
            response=server.error(None,-32600,'Request exceeded the size limit')
        else:
            try:response=server.handle(json.loads(line))
            except (ValueError,UnicodeDecodeError):response=server.error(None,-32700,'Parse error')
        if response is not None:
            sys.stdout.write(json.dumps(response,ensure_ascii=False)+'\n')
            sys.stdout.flush()


if __name__=='__main__':main()
