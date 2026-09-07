import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import httpx

from src.config.settings import settings
from src.services.llm_client import LLMClient, _apply_model_output_mode
from src.services.llm_gateway import gateway, llm_request_context


class ModelTransport(unittest.IsolatedAsyncioTestCase):
    async def test_native_deepseek_gets_explicit_output_mode(self):
        requests = []
        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            with patch('src.services.llm_client._llm_http_client', return_value=http):
                route = SimpleNamespace(model='deepseek-v4-pro',base_url='https://api.deepseek.com/v1', api_key='test',request_timeout_s=1800,connect_timeout_s=1800)
                result = await LLMClient()._complete_openai_compatible(route=route,messages=[{'role':'user','content':'JSON only'}],max_tokens=640,system=None)
        self.assertEqual(result.text,'{"ok":true}')
        self.assertEqual(requests[0]['thinking'],{'type':'disabled'})
        self.assertEqual(requests[0]['max_tokens'],640)

    async def test_vendor_specific_fields_do_not_leak_to_other_providers(self):
        for base,model in [('https://example.com/v1','deepseek-v4-pro'),('https://api.deepseek.com','another-model')]:
            body={}
            _apply_model_output_mode(body,SimpleNamespace(base_url=base,model=model))
            self.assertNotIn('thinking',body)

    async def test_logs_and_activity_pass_fc_boundary_and_surface_rejections(self):
        requests=[]
        def handle(request):
            requests.append(request)
            return httpx.Response(403, json={'message':'Forbidden'})
        constructor=httpx.AsyncClient
        with patch.object(settings,'GAME_SERVICE_UPSTREAM_URL','https://game.internal'), patch.object(settings,'ADMIN_TOKEN','admin'), patch.dict('os.environ',{
            'FC_DEPLOYMENT':'true','FC_INTERNAL_ORIGINS':'https://game.internal','FC_INTERNAL_TOKEN':'a'*64,
        }), patch('src.services.llm_gateway.httpx.AsyncClient',side_effect=lambda **kwargs:constructor(transport=httpx.MockTransport(handle))):
            with llm_request_context(game_id='game',user_id='user',task_id='task'), self.assertLogs('src.services.llm_gateway',level='WARNING'):
                await gateway.emit_llm_call_log({'stepKey':'code_generate.full'})
                await gateway.emit_task_activity({'state':'started'})
        self.assertEqual(len(requests),2)
        for request in requests:
            self.assertEqual(request.headers['x-gamevallies-internal-token'],'a'*64)
            self.assertEqual(request.headers['x-admin-token'],'admin')
            self.assertEqual(json.loads(request.content)['taskId'],'task')
