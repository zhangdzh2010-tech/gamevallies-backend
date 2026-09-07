import unittest
import asyncio
from unittest.mock import patch, AsyncMock
from src.api.models import RunPipelineV2Request, IterateV2Request, GameSpec
from src.engine.interactive_creation import normalize_interactive_request, is_interactive_request, run_interactive, validate_interactive_html

GOOD = '''<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{margin:24px;font:18px sans-serif}button{padding:12px}</style></head><body><h1>种群模型</h1><p>简化模型，不是实验数据</p><output id="count">10</output><button onclick="document.getElementById('count').textContent='20'">调整种群</button><script>let population=10;</script></body></html>'''

class InteractiveCreation(unittest.IsolatedAsyncioTestCase):
    def request(self):
        return RunPipelineV2Request(game_id='game',user_id='user',timeout_s=1800,
            raw_user_input='做一个捕食者与猎物的种群变化模型。\n请生成桌面浏览器中的可交互创意作品。',
            source_spec=GameSpec(game_type='educational',source_description='做题闯关',entities=[]))

    async def test_desktop_brief_replaces_inferred_quiz_contract(self):
        original=self.request()
        request=normalize_interactive_request(original)
        self.assertEqual(request.platform,'desktop_web')
        self.assertEqual(request.runtime_contract.runtime_profile,'interactive_experience')
        self.assertNotIn('game_over',request.runtime_contract.state.required_states)
        self.assertEqual(request.source_spec.rules.scoring,'none')
        self.assertEqual(request.source_spec.entities,[])
        self.assertEqual(original.source_spec.game_type,'educational')
        self.assertEqual(request.prompt_bundle_snapshot.layers['source'],'desktop-interactive-v1')

    async def test_legacy_game_still_uses_original_pipeline(self):
        request=RunPipelineV2Request(game_id='game',user_id='user',raw_user_input='做一个消除闯关游戏')
        self.assertFalse(is_interactive_request(request))
        self.assertIs(normalize_interactive_request(request),request)

    async def test_create_result_requires_real_browser_qa(self):
        request=normalize_interactive_request(self.request())
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',new=AsyncMock(return_value=GOOD)):
            result=await run_interactive(request)
        self.assertTrue(result.qa_passed)
        self.assertEqual(result.runtime_profile,'interactive_experience')
        self.assertTrue(result.runtime_qa_report['contentChanged'])
        self.assertEqual(len(result.runtime_qa_report['viewports']),2)

    async def test_blank_noop_and_broken_script_fail(self):
        noop=GOOD.replace("document.getElementById('count').textContent='20'",'void 0')
        broken=GOOD.replace('let population=10;',"throw new Error('broken simulation')")
        for html in [noop,broken,'<html><body>incomplete']:
            self.assertFalse((await validate_interactive_html(html))['passed'])

    async def test_iteration_keeps_interactive_mode(self):
        source=normalize_interactive_request(self.request()).source_spec
        request=IterateV2Request(game_id='game',user_id='user',current_code=GOOD,timeout_s=1800,
            source_spec=source,iteration_intent={'feedback':'增加重置按钮'})
        request=normalize_interactive_request(request)
        self.assertTrue(is_interactive_request(request))
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',new=AsyncMock(return_value=GOOD)) as call:
            result=await run_interactive(request)
        self.assertEqual(result.changes,['增加重置按钮'])
        self.assertEqual(call.call_args.kwargs['step_key'],'iterate.element_change')

    async def test_async_submission_runs_desktop_pipeline_and_persists_browser_checked_result(self):
        from fakeredis.aioredis import FakeRedis
        from src.services.async_task_store import AsyncTaskRedisStore
        from src.services.async_task_manager import AsyncTaskManager
        from src.api.endpoints import generate
        from src.config.settings import settings
        manager=AsyncTaskManager(store=AsyncTaskRedisStore(client=FakeRedis(decode_responses=True)))
        manager.durable.runners.update(generate.task_manager.durable.runners)
        try:
            with patch.object(generate,'task_manager',manager), patch.object(settings,'GAME_SERVICE_UPSTREAM_URL',''), patch(
                'src.engine.interactive_creation.LLMClient.complete_with_truncation_retry', new=AsyncMock(return_value=GOOD)):
                handle=await generate.run_pipeline_v2_async(self.request(), x_idempotency_key='desktop-e2e')
                for _ in range(200):
                    result=await manager.get_task(handle.task_id)
                    if result.status in {'succeeded','failed'}: break
                    await asyncio.sleep(.05)
                self.assertEqual(result.status,'succeeded',str(result.error))
                self.assertEqual(result.result['runtime_profile'],'interactive_experience')
                self.assertTrue(result.result['runtime_qa_report']['contentChanged'])
        finally:
            await manager.clear()
