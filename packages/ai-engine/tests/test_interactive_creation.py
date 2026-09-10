import unittest
import asyncio
import json
from unittest.mock import patch, AsyncMock
from src.api.models import RunPipelineV2Request, IterateV2Request, GameSpec
from src.engine.interactive_creation import normalize_interactive_request, is_interactive_request, run_interactive, validate_interactive_html, extract_interactive_document

GOOD = '''<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{margin:24px;font:18px sans-serif}button{padding:12px}</style></head><body><h1>种群模型</h1><p>简化模型，不是实验数据</p><output id="count">10</output><button onclick="document.getElementById('count').textContent='20'">调整种群</button><script>let population=10;</script></body></html>'''

async def fake_llm(**kwargs):
    if kwargs.get('step_key') == 'code_review':
        return json.dumps({'artifact_kind':'science','complete':True,'critical_issues':[],
            'scores':{k:8 for k in ['scientific_correctness','parameter_fidelity','explanation_integrity','visual_clarity']},
            'evidence':{k:'Fixture assertion for routing test' for k in ['scientific_correctness','parameter_fidelity','explanation_integrity','visual_clarity']},'issues':[]})
    return GOOD

class InteractiveCreation(unittest.IsolatedAsyncioTestCase):
    async def test_optical_major_arc_is_not_accepted_as_its_labeled_minor_angle(self):
        html = '''<!doctype html><html><body><h1>平面镜</h1>
        <canvas width="300" height="220"></canvas><input type="range" min="15" max="80" value="30" oninput="draw(+this.value)">
        <script>const ctx=document.querySelector('canvas').getContext('2d');
        function draw(a){ctx.clearRect(0,0,300,220);ctx.beginPath();
        ctx.arc(150,100,50,-Math.PI/2-a*Math.PI/180,-Math.PI/2,true);ctx.stroke();
        ctx.fillText('反射角 '+a+'°',10,20);}draw(30);</script></body></html>'''
        bad = await validate_interactive_html(html)
        self.assertFalse(bad['passed'])
        self.assertTrue(any(x['actualSweepDegrees']==330 for x in bad['angleEvidence']))
        good = await validate_interactive_html(html.replace('-Math.PI/2,true)', '-Math.PI/2,false)'))
        self.assertTrue(good['passed'], str(good['issues']))
        self.assertFalse(good['angleEvidence'])
        unrelated = await validate_interactive_html(html.replace('反射角 ', '仪表值 '))
        self.assertFalse(unrelated['angleEvidence'])

    async def test_font_variance_cannot_hide_controls_at_the_bottom_edge(self):
        html = GOOD.replace('body{margin:24px;', 'body{margin:0;').replace('<h1>', '<div style="height:33rem"></div><h1 style="margin:0;font-size:1rem">')
        report = await validate_interactive_html(html)
        self.assertTrue(any('字号容差' in issue for issue in report['issues']))

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
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',new=AsyncMock(side_effect=fake_llm)):
            result=await run_interactive(request)
        self.assertTrue(result.qa_passed)
        self.assertEqual(result.runtime_profile,'interactive_experience')
        self.assertTrue(result.runtime_qa_report['contentChanged'])
        self.assertEqual([(v['width'],v['height']) for v in result.runtime_qa_report['viewports']],
                         [(1000,460),(1000,600),(1366,768),(1920,1080)])

    async def test_blank_noop_and_broken_script_fail(self):
        noop=GOOD.replace("document.getElementById('count').textContent='20'",'void 0')
        broken=GOOD.replace('let population=10;',"throw new Error('broken simulation')")
        for html in [noop,broken,'<html><body>incomplete']:
            self.assertFalse((await validate_interactive_html(html))['passed'])

    async def test_compact_preview_overflow_is_not_hidden_by_large_desktop_checks(self):
        html = GOOD.replace('body{margin:24px;', 'body{min-width:1100px;margin:24px;')
        report = await validate_interactive_html(html)
        self.assertFalse(report['passed'])
        self.assertTrue(report['viewports'][0]['horizontalOverflow'])
        self.assertFalse(report['viewports'][2]['horizontalOverflow'])

    async def test_numeric_input_updates_are_exercised_without_a_button(self):
        html = """<html><head></head><body><h1>电流</h1><input type="number" value="12" min="0" oninput="document.querySelector('output').textContent=this.value/6"><output>2</output><script>const R=6;</script></body></html>"""
        report = await validate_interactive_html(html)
        self.assertTrue(report['passed'], str(report['issues']))
        noop = html.replace('this.value/6', '2')
        self.assertFalse((await validate_interactive_html(noop))['passed'])

    async def test_complete_document_with_inline_handlers_and_trailing_prose(self):
        html = GOOD.replace('<script>let population=10;</script>', '')
        wrapped = '```html\n' + html + '\n```\n以上为完整实现。'
        code = extract_interactive_document(wrapped)
        self.assertEqual(code, html)
        self.assertTrue((await validate_interactive_html(code))['passed'])
        truncated = extract_interactive_document(html.replace('</html>', ''))
        self.assertFalse((await validate_interactive_html(truncated))['passed'])

    async def test_iteration_keeps_interactive_mode(self):
        source=normalize_interactive_request(self.request()).source_spec
        request=IterateV2Request(game_id='game',user_id='user',current_code=GOOD,timeout_s=1800,
            source_spec=source,iteration_intent={'feedback':'增加重置按钮'})
        request=normalize_interactive_request(request)
        self.assertTrue(is_interactive_request(request))
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',new=AsyncMock(side_effect=fake_llm)) as call:
            result=await run_interactive(request)
        self.assertEqual(result.changes,['增加重置按钮'])
        self.assertEqual(call.call_args_list[0].kwargs['step_key'],'iterate.element_change')

    async def test_changing_slider_does_not_hide_a_stalled_start_button(self):
        stalled = '''<!doctype html><html><head></head><body><h1>种群模型</h1>
        <input type="range" oninput="document.querySelector('output').textContent=this.value"><output>50</output>
        <button onclick="this.textContent='正在运行';requestAnimationFrame(frame)">开始</button>
        <p>模拟时间 <span id="clock">0</span></p><script>
        let previous=performance.now(),t=0;
        function frame(now){const elapsed=(now-previous)/1000;previous=now;
          t+=Math.floor(elapsed/5)*0.01;document.querySelector('#clock').textContent=t;
          requestAnimationFrame(frame);}
        </script></body></html>'''
        report=await validate_interactive_html(stalled)
        self.assertTrue(report['contentChanged'])
        self.assertFalse(report['passed'])
        self.assertFalse(report['motionChecks'][0]['advances'])

    async def test_start_with_accumulated_frame_time_advances(self):
        moving = '''<!doctype html><html><head></head><body><h1>模型</h1>
        <button onclick="requestAnimationFrame(frame)">开始</button><output>0</output>
        <script>let previous=performance.now(),accumulator=0,t=0;
        function frame(now){accumulator+=(now-previous)/1000;previous=now;
          while(accumulator>=0.05){t+=1;accumulator-=0.05;}
          document.querySelector('output').textContent=t;requestAnimationFrame(frame);}
        </script></body></html>'''
        report=await validate_interactive_html(moving)
        self.assertTrue(report['passed'],str(report['issues']))
        self.assertTrue(report['motionChecks'][0]['advances'])

    async def test_second_resolution_countdown_is_not_a_stalled_animation(self):
        countdown = '''<!doctype html><html><head></head><body><h1>一盏茶时</h1>
        <button onclick="start()">开始</button><output>120</output>
        <script>let timer;
        function start(){clearInterval(timer);const deadline=Date.now()+120000;
          timer=setInterval(()=>{document.querySelector('output').textContent=
            Math.max(0,Math.ceil((deadline-Date.now())/1000));},1000);}
        </script></body></html>'''
        report=await validate_interactive_html(countdown)
        self.assertTrue(report['passed'],str(report['issues']))
        self.assertTrue(report['motionChecks'][0]['advances'])

    async def test_button_label_change_does_not_count_as_countdown_progress(self):
        stalled = '''<!doctype html><html><head></head><body><h1>一盏茶时</h1>
        <button onclick="this.textContent='暂停';setInterval(()=>{},1000)">开始</button>
        <output>120</output><script>const seconds=120;</script></body></html>'''
        report=await validate_interactive_html(stalled)
        self.assertFalse(report['passed'])
        self.assertFalse(report['motionChecks'][0]['advances'])

    async def test_static_start_action_does_not_require_an_animation(self):
        report=await validate_interactive_html(GOOD.replace('调整种群','开始探索'))
        self.assertTrue(report['passed'])
        self.assertEqual(report['motionChecks'],[])

    async def test_rejected_desktop_candidate_and_report_remain_replayable(self):
        from src.engine.pipeline_errors import PipelineExecutionError
        second = GOOD.replace('<h1>种群模型</h1>', '<h1>第二次候选</h1>')
        failed = {'ran':True,'passed':False,'issues':['simulator stalled']}
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
                   new=AsyncMock(side_effect=[GOOD, json.dumps({'patches':[{'search':'<h1>种群模型</h1>', 'replace':'<h1>第二次候选</h1>'}]})])), patch(
                   'src.engine.interactive_creation.validate_interactive_html',
                   new=AsyncMock(return_value=failed)):
            with self.assertRaises(PipelineExecutionError) as caught:
                await run_interactive(normalize_interactive_request(self.request()))
        self.assertEqual(caught.exception.retry_count, 1)
        artifacts = {item['artifact_type']:item for item in caught.exception.artifacts}
        self.assertEqual(artifacts['failed_interactive_candidate']['payload'], second)
        report = artifacts['interactive_validation_report']['payload']
        self.assertEqual(report['attempt'], 2)
        self.assertFalse(report['runtime']['passed'])
        self.assertIn('simulator stalled', report['runtime']['issues'])
        self.assertIsNone(report['assessment'])

    async def test_native_confirm_is_rejected_even_when_another_control_works(self):
        html = GOOD.replace('</body>', '<button onclick="if(confirm(\'确认\'))document.querySelector(\'output\').textContent=0">清空</button></body>')
        report = await validate_interactive_html(html)
        self.assertFalse(report['passed'])
        self.assertTrue(report['contentChanged'])
        self.assertEqual(report['sandbox'], 'allow-scripts')
        self.assertTrue(report['sandboxViolations'])

    async def test_inline_confirmation_and_isolated_storage_work_in_sandbox(self):
        html = GOOD.replace('</body>', '''<button onclick="document.querySelector('dialog').showModal()">清空</button>
        <dialog><button onclick="localStorage.clear();this.parentElement.close()">确认</button></dialog>
        <script>localStorage.setItem('own-data','1');sessionStorage.setItem('own','2');</script></body>''')
        report = await validate_interactive_html(html)
        self.assertTrue(report['passed'], str(report['issues']))
        self.assertFalse(report['js_errors'])

    async def test_vertical_core_clipping_is_rejected_but_long_explanation_is_allowed(self):
        clipped = GOOD.replace('button{padding:12px}', 'button{padding:12px;margin-top:700px}')
        report = await validate_interactive_html(clipped)
        self.assertFalse(report['passed'])
        self.assertGreater(report['viewports'][1]['coreOutsideCount'], 0)
        self.assertGreater(report['viewports'][1]['hiddenReveal']['coreOutsideCount'], 0)
        explanation = GOOD.replace('</body>', '<p style="height:1200px">次要解释可以在下方阅读。</p></body>')
        self.assertTrue((await validate_interactive_html(explanation))['passed'])

    async def test_blocked_slider_keys_are_not_hidden_by_other_working_controls(self):
        html = GOOD.replace('</body>', '<input type="range" min="0" max="10" value="4" onkeydown="event.preventDefault()"></body>')
        report = await validate_interactive_html(html)
        self.assertFalse(report['passed'])
        self.assertTrue(any('方向键' in issue for issue in report['issues']))

    async def test_reset_must_recompute_initial_nonempty_result(self):
        html = GOOD.replace('</body>', '<button onclick="document.querySelector(\'output\').textContent=\'—\'">重置</button></body>')
        report = await validate_interactive_html(html)
        self.assertFalse(report['passed'])
        self.assertTrue(any('重置未恢复' in issue for issue in report['issues']))
        fixed = html.replace("textContent='—'", "textContent='10'")
        self.assertTrue((await validate_interactive_html(fixed))['passed'])

    async def test_plot_boundaries_are_checked_after_slider_maximum(self):
        html = '''<html><head></head><body><h1>波形</h1><canvas width="400" height="100"></canvas>
        <input type="range" min="1" max="4" value="1" oninput="draw(+this.value)"><script>
        const c=document.querySelector('canvas'),ctx=c.getContext('2d');
        function draw(a){ctx.clearRect(0,0,400,100);ctx.beginPath();
          for(let x=0;x<400;x++)ctx.lineTo(x,50+20*a*Math.sin(x/20));ctx.stroke();}draw(1);
        </script></body></html>'''
        report = await validate_interactive_html(html)
        self.assertFalse(report['passed'])
        self.assertTrue(report['drawingIssues'])
        self.assertTrue((await validate_interactive_html(html.replace('20*a*Math.sin','10*a*Math.sin')))['passed'])

    async def test_local_patch_is_used_instead_of_second_full_generation(self):
        failed = {'ran':True,'passed':False,'issues':['修正标题']}
        passed = {'ran':True,'passed':True,'issues':[]}
        patch_json = json.dumps({'patches':[{'search':'<h1>种群模型</h1>','replace':'<h1>生态观察</h1>'}]})
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(side_effect=[GOOD,patch_json,await fake_llm(step_key='code_review')])) as llm, patch(
            'src.engine.interactive_creation.validate_interactive_html',new=AsyncMock(side_effect=[failed,passed])):
            result = await run_interactive(normalize_interactive_request(self.request()))
        self.assertEqual([c.kwargs['step_key'] for c in llm.call_args_list],
                         ['code_generate.full','quality_gate.patch_fix','code_review'])
        self.assertIn('<h1>生态观察</h1>', result.html_code)
        self.assertEqual(result.runtime_qa_report['generationAttempts'],
                         {'fullGenerationCalls':1,'patchCalls':1,'qaAttempts':2})

    async def test_invalid_patch_keeps_original_candidate_and_does_not_regenerate(self):
        from src.engine.pipeline_errors import PipelineExecutionError
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(side_effect=[GOOD,'not json'])) as llm, patch(
            'src.engine.interactive_creation.validate_interactive_html',
            new=AsyncMock(return_value={'ran':True,'passed':False,'issues':['problem']})):
            with self.assertRaises(PipelineExecutionError) as caught:
                await run_interactive(normalize_interactive_request(self.request()))
        self.assertEqual(llm.call_count, 2)
        self.assertEqual(next(a['payload']['runtime']['generationAttempts'] for a in caught.exception.artifacts
                             if a['artifact_type']=='interactive_validation_report'),
                         {'fullGenerationCalls':1,'patchCalls':1,'qaAttempts':1})
        self.assertEqual(next(a['payload'] for a in caught.exception.artifacts
                             if a['artifact_type']=='failed_interactive_candidate'),GOOD)

    async def test_review_infrastructure_failure_retains_candidate_without_regeneration(self):
        from src.engine.pipeline_errors import PipelineExecutionError
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(side_effect=[GOOD,RuntimeError('review unavailable')])) as llm, patch(
            'src.engine.interactive_creation.validate_interactive_html',
            new=AsyncMock(return_value={'ran':True,'passed':True,'issues':[]})):
            with self.assertRaises(PipelineExecutionError) as caught:
                await run_interactive(normalize_interactive_request(self.request()))
        self.assertEqual(caught.exception.failure_family,'review_infrastructure')
        self.assertEqual(llm.call_count,2)
        self.assertEqual(next(a['payload'] for a in caught.exception.artifacts
                             if a['artifact_type']=='failed_interactive_candidate'),GOOD)

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
                'src.engine.interactive_creation.LLMClient.complete_with_truncation_retry', new=AsyncMock(side_effect=fake_llm)):
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
