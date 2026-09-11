import unittest
import asyncio
import json
from unittest.mock import patch, AsyncMock
from src.api.models import RunPipelineV2Request, IterateV2Request, GameSpec
from src.engine.interactive_creation import normalize_interactive_request, is_interactive_request, run_interactive, validate_interactive_html, extract_interactive_document
from src.engine.source_references import indexed_review_source, source_reference_catalog

GOOD = '''<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{margin:24px;font:18px sans-serif}button{padding:12px}</style></head><body><h1>种群模型</h1><p>简化模型，不是实验数据</p><output id="count">10</output><button onclick="document.getElementById('count').textContent='20'">调整种群</button><script>let population=10;</script></body></html>'''

async def fake_llm(**kwargs):
    if kwargs.get('step_key') == 'code_review':
        return json.dumps({'artifact_kind':'science','complete':True,'critical_issues':[],
            'scores':{k:8 for k in ['scientific_correctness','parameter_fidelity','explanation_integrity','visual_clarity']},
            'evidence':{k:'Fixture assertion for routing test' for k in ['scientific_correctness','parameter_fidelity','explanation_integrity','visual_clarity']},'issues':[]})
    if kwargs.get('step_key') == 'iterate.element_change':
        return json.dumps({'patches':[{'search':'</body>', 'replace':
            '<button onclick="document.getElementById(\'count\').textContent=\'10\'">重置</button></body>'}]})
    return GOOD

class InteractiveCreation(unittest.IsolatedAsyncioTestCase):
    async def test_rendered_canvas_text_evidence_measures_overlap_and_clipping(self):
        html = GOOD.replace('</body>', '''<canvas width="300" height="150"></canvas><script>
        const c=document.querySelector('canvas').getContext('2d');c.font='20px sans-serif';
        c.fillText('WIDTH',20,35);c.fillText('1920px',20,35);
        c.fillText('CLIPPED',290,70);
        c.fillText('Shadow',20,100);c.fillText('Shadow',21,101);
        </script></body>''')
        report = await validate_interactive_html(html)
        evidence = report['canvasTextEvidence']
        self.assertTrue(any(e['type']=='canvas_text_overlap' and 'WIDTH' in e['texts'] for e in evidence))
        self.assertTrue(any(e['type']=='canvas_text_clipped' and e['text']=='CLIPPED' for e in evidence))
        self.assertFalse(any(e['type']=='canvas_text_overlap' and e['texts']==['Shadow','Shadow'] for e in evidence))
        # Evidence is measured; intentional art overlap still needs semantic judgement.
        self.assertTrue(report['passed'], str(report['issues']))

    async def test_cleared_text_is_not_reported_as_overlap_with_next_frame(self):
        html = GOOD.replace('</body>', '''<canvas width="300" height="150"></canvas><script>
        const c=document.querySelector('canvas').getContext('2d');c.font='20px sans-serif';
        c.fillText('old',20,35);c.clearRect(0,0,300,150);c.fillText('new',20,35);
        </script></body>''')
        self.assertFalse((await validate_interactive_html(html))['canvasTextEvidence'])

    async def test_invalid_review_corrects_assessment_without_touching_artifact(self):
        valid = await fake_llm(step_key='code_review')
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(side_effect=[GOOD,'not JSON',valid])) as llm, patch(
            'src.engine.interactive_creation.validate_interactive_html',
            new=AsyncMock(return_value={'ran':True,'passed':True,'issues':[]})) as qa:
            result = await run_interactive(normalize_interactive_request(self.request()))
        self.assertEqual(result.html_code, GOOD)
        self.assertEqual(qa.await_count, 1)
        self.assertEqual([c.kwargs['step_key'] for c in llm.call_args_list],
            ['code_generate.full','code_review','code_review'])
        self.assertIn('SAME complete source', llm.call_args_list[-1].kwargs['messages'][0]['content'])
        self.assertEqual(result.quality_breakdown['reviewRequests'], 2)

    async def test_invalid_review_exhaustion_never_spends_code_repair_budget(self):
        from src.engine.pipeline_errors import PipelineExecutionError
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(side_effect=[GOOD,'{}','{}'])) as llm, patch(
            'src.engine.interactive_creation.validate_interactive_html',
            new=AsyncMock(return_value={'ran':True,'passed':True,'issues':[]})):
            with self.assertRaises(PipelineExecutionError) as caught:
                await run_interactive(normalize_interactive_request(self.request()))
        self.assertEqual(caught.exception.failure_family,'review_evidence')
        self.assertEqual(llm.await_count,3)
        self.assertEqual(next(a['payload'] for a in caught.exception.artifacts
            if a['artifact_type']=='failed_interactive_candidate'),GOOD)

    async def test_invented_requirement_corrects_review_without_repairing_valid_source(self):
        valid = json.loads(await fake_llm(step_key='code_review'))
        invalid = dict(valid, complete=False, critical_issues=['必须保存历史记录'], findings=[
            dict(issue='必须保存历史记录', dimension='complete', basis='requirement',
                requirement_quote='保存全部历史记录',
                source_ref=next(iter(source_reference_catalog(GOOD))),
                reason='审核自行假设必须保存历史记录。', correction='增加历史记录。')])
        valid['suggestions'] = ['可选增加历史记录']
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(side_effect=[GOOD,json.dumps(invalid),json.dumps(valid)])) as llm, patch(
            'src.engine.interactive_creation.validate_interactive_html',
            new=AsyncMock(return_value={'ran':True,'passed':True,'issues':[]})) as qa:
            result = await run_interactive(normalize_interactive_request(self.request()))
        self.assertEqual(result.html_code, GOOD)
        self.assertEqual(qa.await_count, 1)
        self.assertEqual([c.kwargs['step_key'] for c in llm.call_args_list],
            ['code_generate.full','code_review','code_review'])
        self.assertEqual(result.quality_breakdown['suggestions'], ['可选增加历史记录'])
        self.assertEqual(result.quality_breakdown['issues'], [])

    async def test_layout_repair_rejects_dom_deletion_then_corrects_same_source(self):
        bad = json.dumps({'patches':[{'search':'id="count"','replace':''}]})
        good = json.dumps({'patches':[{'search':'margin:24px','replace':'margin:12px'}]})
        runtime = {'ran':True,'passed':False,'issues':['layout overflow'],
            'layoutIssues':['layout overflow'],'js_errors':[],'contentChanged':True}
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(side_effect=[GOOD,bad,good,await fake_llm(step_key='code_review')])) as llm, patch(
            'src.engine.interactive_creation.validate_interactive_html', new=AsyncMock(side_effect=[
                runtime,dict(runtime,passed=True,issues=[],layoutIssues=[])])) as qa:
            result = await run_interactive(normalize_interactive_request(self.request()))
        self.assertEqual(result.html_code, GOOD.replace('margin:24px','margin:12px'))
        self.assertEqual(qa.await_count,2)
        self.assertEqual([c.kwargs['step_key'] for c in llm.call_args_list],
            ['code_generate.full','quality_gate.patch_fix','quality_gate.patch_fix','code_review'])
        self.assertIn('layout_scope',llm.call_args_list[2].kwargs['messages'][0]['content'])

    async def test_layout_scope_exhaustion_cannot_fall_back_to_full_rewrite(self):
        from src.engine.pipeline_errors import PipelineExecutionError
        bad = json.dumps({'patches':[{'search':'id="count"','replace':''}]})
        runtime = {'ran':True,'passed':False,'issues':['layout overflow'],'layoutIssues':['layout overflow']}
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(side_effect=[GOOD,bad,bad])) as llm, patch(
            'src.engine.interactive_creation.validate_interactive_html', new=AsyncMock(return_value=runtime)) as qa:
            with self.assertRaises(PipelineExecutionError) as caught:
                await run_interactive(normalize_interactive_request(self.request()))
        self.assertEqual(caught.exception.failure_family,'repair_protocol')
        self.assertEqual(llm.await_count,3)
        self.assertEqual(qa.await_count,1)
        self.assertEqual(next(a['payload'] for a in caught.exception.artifacts
            if a['artifact_type']=='failed_interactive_candidate'),GOOD)

    async def test_runtime_regression_retains_last_valid_candidate(self):
        from src.engine.pipeline_errors import PipelineExecutionError
        review = json.loads(await fake_llm(step_key='code_review'))
        review['critical_issues'] = ['required control missing']
        review['findings'] = [dict(issue='required control missing', dimension='critical_issue',
            basis='requirement', requirement_quote='种群变化模型',
            source_ref=next(iter(source_reference_catalog(GOOD))),
            reason='Required population control is missing in this synthetic assessment.',
            correction='Add the requested population control.')]
        delta = json.dumps({'patches':[{'search':'let population=10;','replace':"throw new Error('regression');"}]})
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(side_effect=[GOOD,json.dumps(review),delta])), patch(
            'src.engine.interactive_creation.validate_interactive_html',new=AsyncMock(side_effect=[
                {'ran':True,'passed':True,'issues':[]},
                {'ran':True,'passed':False,'issues':['regression']}])):
            with self.assertRaises(PipelineExecutionError) as caught:
                await run_interactive(normalize_interactive_request(self.request()))
        self.assertEqual(next(a['payload'] for a in caught.exception.artifacts
            if a['artifact_type']=='failed_interactive_candidate'),GOOD)
        self.assertTrue(any(a.get('metadata',{}).get('discardedRegression') for a in caught.exception.artifacts))

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
        self.assertTrue(any('字号容差' in issue for issue in report['layoutIssues']))

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

    async def test_title_iteration_preserves_every_unrelated_byte_and_runs_real_browser_qa(self):
        source = normalize_interactive_request(self.request()).source_spec
        request = normalize_interactive_request(IterateV2Request(game_id='game',user_id='user',
            current_code=GOOD, source_spec=source, iteration_intent={'feedback':'只修改标题为生态观察'}))
        delta = json.dumps({'patches':[{'search':'<h1>种群模型</h1>','replace':'<h1>生态观察</h1>'}]})
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(side_effect=[delta, await fake_llm(step_key='code_review')])) as llm:
            result = await run_interactive(request)
        self.assertEqual(result.html_code, GOOD.replace('<h1>种群模型</h1>', '<h1>生态观察</h1>'))
        self.assertEqual(result.runtime_qa_report['generationAttempts'],
                         {'fullGenerationCalls':0,'patchCalls':1,'qaAttempts':1})
        self.assertTrue(result.runtime_qa_report['contentChanged'])
        self.assertEqual(llm.call_args_list[0].kwargs['max_tokens'],4096)
        self.assertEqual(llm.call_args_list[0].kwargs['response_size_hint'],'large_patch')

    async def test_iteration_protocol_correction_uses_unchanged_source_once(self):
        source = normalize_interactive_request(self.request()).source_spec
        request = normalize_interactive_request(IterateV2Request(game_id='game',user_id='user',
            current_code=GOOD, source_spec=source, iteration_intent={'feedback':'只修改标题为生态观察'}))
        delta = json.dumps({'patches':[{'search':'<h1>种群模型</h1>','replace':'<h1>生态观察</h1>'}]})
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(side_effect=['not JSON',delta,await fake_llm(step_key='code_review')])) as llm, patch(
            'src.engine.interactive_creation.validate_interactive_html',
            new=AsyncMock(return_value={'ran':True,'passed':True,'issues':[]})) as qa:
            result = await run_interactive(request)
        self.assertEqual(result.html_code,GOOD.replace('种群模型','生态观察'))
        self.assertEqual(qa.await_count,1)
        correction = json.loads(llm.call_args_list[1].kwargs['messages'][0]['content'].split('\n',1)[1])
        self.assertEqual(correction['html'],indexed_review_source(GOOD))
        self.assertIn('只修改标题为生态观察',correction['brief'])
        self.assertEqual(result.runtime_qa_report['generationAttempts'],
                         {'fullGenerationCalls':0,'patchCalls':2,'qaAttempts':1})

    async def test_repeated_invalid_iteration_never_rewrites_or_loses_the_source(self):
        from src.engine.pipeline_errors import PipelineExecutionError
        source = normalize_interactive_request(self.request()).source_spec
        request = normalize_interactive_request(IterateV2Request(game_id='game',user_id='user',
            current_code=GOOD, source_spec=source, iteration_intent={'feedback':'只修改标题为生态观察'}))
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(side_effect=['not JSON','not JSON'])) as llm, patch(
            'src.engine.interactive_creation.validate_interactive_html',new=AsyncMock()) as qa:
            with self.assertRaises(PipelineExecutionError) as caught:
                await run_interactive(request)
        self.assertEqual(llm.await_count,2)
        self.assertEqual(qa.await_count,0)
        self.assertEqual(next(a['payload'] for a in caught.exception.artifacts
                             if a['artifact_type']=='failed_interactive_candidate'),GOOD)

    async def test_iteration_followup_repair_keeps_the_edit_request_and_current_candidate(self):
        source = normalize_interactive_request(self.request()).source_spec
        request = normalize_interactive_request(IterateV2Request(game_id='game',user_id='user',
            current_code=GOOD, source_spec=source, iteration_intent={'feedback':'修改标题为生态观察，修复布局'}))
        delta = json.dumps({'patches':[{'search':'<h1>种群模型</h1>','replace':'<h1>生态观察</h1>'}]})
        repair = json.dumps({'patches':[{'search':'margin:24px','replace':'margin:16px'}]})
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(side_effect=[delta,repair,await fake_llm(step_key='code_review')])) as llm, patch(
            'src.engine.interactive_creation.validate_interactive_html',new=AsyncMock(side_effect=[
                {'ran':True,'passed':False,'issues':['布局溢出']}, {'ran':True,'passed':True,'issues':[]}])):
            result = await run_interactive(request)
        correction = json.loads(llm.call_args_list[1].kwargs['messages'][0]['content'].split('\n',1)[1])
        self.assertIn('修改标题为生态观察，修复布局',correction['brief'])
        self.assertEqual(correction['html'],indexed_review_source(GOOD.replace('种群模型','生态观察')))
        self.assertIn('布局溢出',correction['issues'])
        self.assertEqual(result.html_code,GOOD.replace('种群模型','生态观察').replace('margin:24px','margin:16px'))

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

    async def test_plot_stroke_at_boundary_is_checked_with_canvas_transform(self):
        html = '''<html><body><h1>波形</h1><canvas width="400" height="100"></canvas>
        <input type="range" min="1" max="2" value="1" oninput="draw(+this.value)"><script>
        const ctx=document.querySelector('canvas').getContext('2d');ctx.scale(2,2);ctx.lineWidth=3;
        function draw(a){ctx.clearRect(0,0,200,50);ctx.beginPath();
          for(let x=0;x<200;x++)ctx.lineTo(x,25+12.5*a*Math.sin(x/10));ctx.stroke();}draw(1);
        </script></body></html>'''
        report = await validate_interactive_html(html)
        self.assertFalse(report['passed'])
        self.assertTrue(any(x['strokeClipped'] and x['strokeRadiusY']==3 for x in report['drawingIssues']))
        fixed = await validate_interactive_html(html.replace('12.5*a*Math.sin','10*a*Math.sin'))
        self.assertTrue(fixed['passed'], str(fixed['issues']))

    async def test_dense_axes_and_path2d_are_not_misread_as_clipped_plot(self):
        script = '''<canvas width="400" height="100"></canvas><script>
        const ctx=document.querySelector('canvas').getContext('2d');ctx.lineWidth=4;
        ctx.beginPath();for(let x=0;x<400;x++)ctx.lineTo(x,x/4);ctx.stroke();
        ctx.beginPath();for(let x=0;x<400;x++){ctx.moveTo(x,-10);ctx.lineTo(x,110);}ctx.stroke();
        ctx.beginPath();for(let x=0;x<400;x++)ctx.lineTo(x,50+70*Math.sin(x/20));
        const separate=new Path2D();separate.rect(20,20,40,40);ctx.stroke(separate);
        </script>'''
        report = await validate_interactive_html(GOOD.replace('</body>',script+'</body>'))
        self.assertFalse(report['drawingIssues'])

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

    async def test_invalid_create_patch_corrects_against_retained_source(self):
        delta = json.dumps({'patches':[{'search':'<h1>种群模型</h1>','replace':'<h1>生态观察</h1>'}]})
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(side_effect=[GOOD,'not json',delta,await fake_llm(step_key='code_review')])) as llm, patch(
            'src.engine.interactive_creation.validate_interactive_html',
            new=AsyncMock(side_effect=[{'ran':True,'passed':False,'issues':['original defect']},
                                     {'ran':True,'passed':True,'issues':[]}])):
            result = await run_interactive(normalize_interactive_request(self.request()))
        self.assertIn('生态观察', result.html_code)
        correction = json.loads(llm.call_args_list[2].kwargs['messages'][0]['content'].split('\n',1)[1])
        self.assertEqual(correction['html'], indexed_review_source(GOOD))
        self.assertIn('original defect', correction['issues'])
        self.assertEqual(result.runtime_qa_report['generationAttempts'],
                         {'fullGenerationCalls':1,'patchCalls':2,'qaAttempts':2})

    async def test_exhausted_create_patch_protocol_regenerates_and_revalidates(self):
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(side_effect=[GOOD,'not json','not json',GOOD,await fake_llm(step_key='code_review')])) as llm, patch(
            'src.engine.interactive_creation.validate_interactive_html',
            new=AsyncMock(side_effect=[{'ran':True,'passed':False,'issues':['original defect']},
                                     {'ran':True,'passed':True,'issues':[]}])) as qa:
            result = await run_interactive(normalize_interactive_request(self.request()))
        self.assertEqual([c.kwargs['step_key'] for c in llm.call_args_list],
                         ['code_generate.full','quality_gate.patch_fix','quality_gate.patch_fix','code_generate.full','code_review'])
        self.assertIn('original defect',llm.call_args_list[3].kwargs['messages'][0]['content'])
        self.assertEqual(qa.await_count, 2)
        self.assertEqual(result.runtime_qa_report['generationAttempts'],
                         {'fullGenerationCalls':2,'patchCalls':2,'qaAttempts':2})

    async def test_failed_regeneration_does_not_restart_the_budget_or_lose_evidence(self):
        from src.engine.pipeline_errors import PipelineExecutionError
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(side_effect=[GOOD,'not json','not json',GOOD])) as llm, patch(
            'src.engine.interactive_creation.validate_interactive_html',
            new=AsyncMock(return_value={'ran':True,'passed':False,'issues':['original defect']})):
            with self.assertRaises(PipelineExecutionError) as caught:
                await run_interactive(normalize_interactive_request(self.request()))
        self.assertEqual(llm.call_count, 4)
        self.assertIn('original defect', str(caught.exception))
        self.assertEqual(len([a for a in caught.exception.artifacts
                             if a['artifact_type']=='interactive_repair_protocol_report']),2)
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

    async def test_standard_create_soft_fails_runtime_infrastructure_when_optional(self):
        with patch('src.engine.interactive_creation.settings.RUNTIME_QA_REQUIRED', False), patch(
            'src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(side_effect=[GOOD, await fake_llm(step_key='code_review')])), patch(
            'src.engine.interactive_creation.validate_interactive_html',
            new=AsyncMock(side_effect=TimeoutError())):
            result = await run_interactive(normalize_interactive_request(self.request()))
        self.assertTrue(result.qa_passed)
        self.assertTrue(result.runtime_qa_report['softFailed'])
        self.assertEqual(result.runtime_qa_report['unavailableReason'], 'TimeoutError')

    async def test_required_runtime_qa_still_fails_closed_on_infrastructure_timeout(self):
        from src.engine.pipeline_errors import PipelineExecutionError
        with patch('src.engine.interactive_creation.settings.RUNTIME_QA_REQUIRED', True), patch(
            'src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
            new=AsyncMock(return_value=GOOD)), patch(
            'src.engine.interactive_creation.validate_interactive_html',
            new=AsyncMock(side_effect=TimeoutError())):
            with self.assertRaises(PipelineExecutionError) as caught:
                await run_interactive(normalize_interactive_request(self.request()))
        self.assertEqual(caught.exception.failure_family, 'runtime_infrastructure')

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

class GroundedResetAcceptance(unittest.IsolatedAsyncioTestCase):
    BRIEF = '调整参数；重置恢复全部初始状态。'
    HTML = '''<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
    <h1>参数模型</h1><label>速度<input id="speed" type="range" min="0.05" max="0.45" step="0.01" value="0.15"></label>
    <output id="value">0.15</output><button id="reset">↺ 重置</button>
    <script>const s=document.getElementById('speed'),o=document.getElementById('value');
    s.oninput=()=>o.textContent=s.value;
    document.getElementById('reset').onclick=()=>{o.textContent=s.value;};</script></body></html>'''

    def test_requirement_is_opt_in_and_preserves_explicit_exceptions(self):
        from src.engine.interactive_contract_probes import reset_requirement
        self.assertEqual(reset_requirement(self.BRIEF), '重置恢复全部初始状态')
        self.assertIsNone(reset_requirement('提供重置按钮'))
        self.assertIsNone(reset_requirement('不要重置恢复全部初始状态。'))
        self.assertIsNone(reset_requirement(self.BRIEF+'重置时保留用户选择的预设。'))
        self.assertIsNone(reset_requirement('Do not reset restore all initial state.'))
        self.assertEqual(reset_requirement('Reset must restore all initial state.'),'Reset must restore all initial state')

    async def test_real_transition_catches_reset_that_only_updates_status(self):
        bad = await validate_interactive_html(self.HTML, brief=self.BRIEF)
        checks = [x for x in bad['contractChecks'] if x['contract']=='reset_all_inputs_v1']
        self.assertEqual(len(checks),1)
        self.assertEqual(checks[0]['status'],'failed')
        self.assertEqual(checks[0]['expected'][0]['value'],'0.15')
        self.assertEqual(checks[0]['observed'][0]['value'],'0.45')
        self.assertFalse(bad['passed'])
        self.assertNotEqual(set(bad['issues']),set(bad['layoutIssues']))
        fixed = self.HTML.replace('()=>{o.textContent=s.value;}',"()=>{s.value='0.15';o.textContent=s.value;}")
        good = await validate_interactive_html(fixed, brief=self.BRIEF)
        self.assertTrue(good['passed'],good['issues'])
        self.assertEqual(good['contractChecks'][-1]['status'],'passed')

    async def test_reset_probe_does_not_invent_requirement_or_ambiguous_scope(self):
        allowed = await validate_interactive_html(self.HTML)
        self.assertTrue(allowed['passed'],allowed['issues'])
        self.assertFalse(allowed['contractChecks'])
        ambiguous = self.HTML.replace('</body>','<button>全部重置</button></body>')
        report = await validate_interactive_html(ambiguous, brief=self.BRIEF)
        self.assertEqual(report['contractChecks'][-1]['status'],'unverified')
        self.assertIn('unique',report['contractChecks'][-1]['reason'])

    async def test_form_reset_restores_number_text_select_and_checkbox(self):
        html = self.HTML.replace('<label>速度','<form id="params"><label>速度').replace(
            '<button id="reset">', '<input type="number" value="4" min="1" max="8"><input type="text" value="初值">'
            '<select><option value="a">甲</option><option value="b">乙</option></select>'
            '<input type="checkbox" checked><button type="button" id="reset">').replace(
            '<script>', '</form><script>').replace(
            '()=>{o.textContent=s.value;}', "()=>{HTMLFormElement.prototype.reset.call(document.getElementById('params'));o.textContent=s.value;}")
        report = await validate_interactive_html(html, brief=self.BRIEF)
        check = report['contractChecks'][-1]
        self.assertEqual(check['status'],'passed',check)
        self.assertEqual(len(check['expected']),5)
        self.assertTrue(all(a['value']!=b['value'] for a,b in zip(check['input'],check['expected'])))
        self.assertEqual(check['expected'],check['observed'])

class IsolatedMotionScenarios(unittest.IsolatedAsyncioTestCase):
    HTML = '''<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><h1>热平衡模型</h1>
    <label>左温度<input id="left" type="range" min="0" max="1" step="0.1" value="0"></label>
    <label>右温度<input id="right" type="range" min="0" max="1" step="0.1" value="1"></label>
    <button id="start">开始</button><output id="temperature">0.500000</output><span id="notice"></span>
    <script>let running=false,temperature=0.5;
    const left=document.getElementById('left'),right=document.getElementById('right'),out=document.getElementById('temperature');
    function frame(){if(!running)return;temperature+=( (Number(left.value)+Number(right.value))/2-temperature)*0.1;
      out.textContent=temperature.toFixed(6);requestAnimationFrame(frame);}
    function edit(){running=false;document.getElementById('notice').textContent='参数已调整';}
    left.oninput=right.oninput=edit;
    document.getElementById('start').onclick=()=>{running=!running;if(running)requestAnimationFrame(frame);};
    </script></body></html>'''

    async def test_equilibrium_is_perturbed_in_a_fresh_scenario_before_start(self):
        report=await validate_interactive_html(self.HTML)
        self.assertTrue(report['passed'],report['issues'])
        motion=report['motionChecks'][0]
        self.assertFalse(motion['scenarios'][0]['advances'])
        self.assertTrue(motion['advances'])
        self.assertEqual(motion['scenarios'][-1]['setup'],'single_parameter_edit')
        self.assertEqual(motion['scenarios'][-1]['parameter'],'left')
        self.assertEqual(motion['scenarios'][-1]['before'],'0')
        self.assertEqual(motion['scenarios'][-1]['after'],'0.1')

    async def test_boundary_stress_state_does_not_contaminate_initial_start(self):
        html=self.HTML.replace('running=false,temperature=0.5','running=false,temperature=0').replace(
            'function edit(){running=false;', 'function edit(){running=false;temperature=(Number(left.value)+Number(right.value))/2;')
        report=await validate_interactive_html(html)
        self.assertTrue(report['passed'],report['issues'])
        self.assertEqual(len(report['motionChecks'][0]['scenarios']),1)
        self.assertTrue(report['motionChecks'][0]['scenarios'][0]['advances'])


def _pendulum_html(*, theta=0.0, start='<button id="start">开始</button>', extra_input='', start_js='running=true;'):
    return f'''<!doctype html><html><body>
    <h1>单摆</h1><p>小角度理想单摆，T=2π√(L/g)，不是实验数据</p>
    <label>摆长L<input id="L" type="range" min="0.5" max="2" step="0.1" value="1"></label>
    <label>重力g<input id="g" type="range" min="5" max="15" step="0.1" value="9.8"></label>
    {extra_input}<output id="period">2.007</output>
    {start}<button id="pause">暂停</button><button id="reset">重置</button>
    <canvas id="c" width="220" height="180"></canvas>
    <script>
    let L=1,g=9.8,theta={theta},omega=0,running=false,prev=performance.now(),acc=0;
    const ctx=document.getElementById('c').getContext('2d');
    function draw(){{
      ctx.clearRect(0,0,220,180);
      const x=110+80*Math.sin(theta),y=20+80*Math.cos(theta);
      ctx.beginPath();ctx.moveTo(110,20);ctx.lineTo(x,y);ctx.stroke();
      ctx.beginPath();ctx.arc(x,y,8,0,6.28);ctx.fill();
      document.getElementById('period').textContent=(2*Math.PI*Math.sqrt(L/g)).toFixed(3);
    }}
    function frame(now){{
      acc+=(now-prev)/1000;prev=now;
      while(acc>=0.01){{acc-=0.01;if(running){{omega+=-(g/L)*theta*0.01;theta+=omega*0.01;}}}}
      draw();requestAnimationFrame(frame);
    }}
    requestAnimationFrame(frame);
    document.getElementById('L').oninput=e=>{{L=+e.target.value;draw();}};
    document.getElementById('g').oninput=e=>{{g=+e.target.value;draw();}};
    document.getElementById('start').onclick=()=>{{{start_js}}};
    document.getElementById('pause').onclick=()=>{{running=false;}};
    document.getElementById('reset').onclick=()=>{{running=false;theta={theta};omega=0;draw();}};
    </script></body></html>'''


class ScienceDemoMotionContract(unittest.IsolatedAsyncioTestCase):
    async def test_resting_pendulum_still_fails_after_length_and_gravity_edits(self):
        report = await validate_interactive_html(_pendulum_html(theta=0))
        self.assertFalse(report['passed'])
        self.assertFalse(report['motionChecks'][0]['advances'])
        self.assertTrue(any(s['setup']=='single_parameter_edit' for s in report['motionChecks'][0]['scenarios']))
        self.assertTrue(any('非平衡' in issue for issue in report['issues']))

    async def test_displaced_pendulum_start_advances_without_drag(self):
        report = await validate_interactive_html(_pendulum_html(theta=0.4))
        self.assertTrue(report['passed'], report['issues'])
        self.assertTrue(report['motionChecks'][0]['advances'])

    async def test_number_initial_angle_is_perturbed_before_declaring_stall(self):
        html = _pendulum_html(
            extra_input='<label>初角<input id="theta0" type="number" min="0" max="0.5" step="0.1" value="0"></label>',
            start_js="theta=+document.getElementById('theta0').value;running=true;")
        report = await validate_interactive_html(html)
        self.assertTrue(report['passed'], report['issues'])
        motion = report['motionChecks'][0]
        self.assertFalse(motion['scenarios'][0]['advances'])
        self.assertEqual(motion['scenarios'][-1]['parameter'], 'theta0')
        self.assertTrue(motion['advances'])

    async def test_icon_only_start_is_discovered_and_still_must_animate(self):
        dead = GOOD.replace(
            '<button onclick="document.getElementById(\'count\').textContent=\'20\'">调整种群</button>',
            '<button id="start" aria-label="开始" onclick="this.textContent=\'运行中\'">▶</button>'
        ).replace('let population=10;', 'function unused(){requestAnimationFrame(()=>{})}')
        report = await validate_interactive_html(dead)
        self.assertFalse(report['passed'])
        self.assertEqual(report['motionChecks'][0]['control'], '开始')
        self.assertFalse(report['motionChecks'][0]['advances'])

    async def test_font_stress_layout_compact_is_applied_before_llm_repair(self):
        failed = {
            'ran': True, 'passed': False,
            'issues': ['1000×600 字号容差检查（根字号+12.5%）核心图形/控件溢出。'],
            'layoutIssues': ['1000×600 字号容差检查（根字号+12.5%）核心图形/控件溢出。'],
        }
        passed = {'ran': True, 'passed': True, 'issues': [], 'layoutIssues': []}
        request = normalize_interactive_request(RunPipelineV2Request(
            game_id='game', user_id='user', timeout_s=1800,
            raw_user_input='作品类型：科学演示。做一个单摆实验。',
            source_spec=GameSpec(game_type='interactive_experience', source_description='单摆')))
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
                   new=AsyncMock(side_effect=[GOOD, await fake_llm(step_key='code_review')])) as llm, patch(
                'src.engine.interactive_creation.validate_interactive_html',
                new=AsyncMock(side_effect=[failed, passed])) as qa:
            result = await run_interactive(request)
        self.assertIn('data-work-layout-compact', result.html_code)
        self.assertEqual(qa.await_count, 2)
        self.assertEqual([c.kwargs['step_key'] for c in llm.call_args_list],
                         ['code_generate.full', 'code_review'])

    async def test_science_generation_prompt_includes_runtime_contract(self):
        request = normalize_interactive_request(RunPipelineV2Request(
            game_id='game', user_id='user', timeout_s=1800,
            raw_user_input='作品类型：科学演示。制作小角度理想单摆演示，可调摆长L和重力g，周期T=2π√(L/g)，有开始暂停重置。',
            source_spec=GameSpec(game_type='interactive_experience', source_description='单摆')))
        self.assertEqual(request.artifact_kind, 'science')
        self.assertIn('非平衡', request.prompt_bundle_snapshot.layers['system_prompt'])
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
                   new=AsyncMock(side_effect=[GOOD, await fake_llm(step_key='code_review')])) as llm, patch(
                'src.engine.interactive_creation.validate_interactive_html',
                new=AsyncMock(return_value={'ran':True,'passed':True,'issues':[]})):
            await run_interactive(request)
        self.assertIn('非平衡', llm.call_args_list[0].kwargs['system'])
        self.assertIn('不要把拖拽', llm.call_args_list[0].kwargs['system'])

    async def test_font_stress_overflow_is_compacted_without_shrinking_type(self):
        tall = '''<!doctype html><html><head><meta charset="utf-8">
        <style>body{margin:16px;font:16px/1.4 sans-serif}
        canvas{width:960px;height:520px;display:block}</style></head>
        <body><h1>单摆</h1>
        <p>小角度理想单摆，T=2π√(L/g)。</p>
        <canvas id="c" width="960" height="520"></canvas>
        <label>摆长L<input id="L" type="range" min="0.5" max="2" step="0.1" value="1"></label>
        <label>重力g<input id="g" type="range" min="5" max="15" step="0.1" value="9.8"></label>
        <button id="start">开始</button><button id="pause">暂停</button><button id="reset">重置</button>
        <output id="period">2.007</output>
        <script>document.getElementById('start').onclick=()=>{document.getElementById('period').textContent='2.100'};</script>
        </body></html>'''
        from src.engine.interactive_creation import apply_preview_layout_compact
        original = await validate_interactive_html(tall)
        self.assertTrue(any('字号容差' in issue or '核心图形' in issue for issue in original['issues']), original['issues'])
        compact = apply_preview_layout_compact(tall)
        self.assertNotIn('font-size', compact.split('data-work-layout-compact', 1)[-1][:400])
        repaired = await validate_interactive_html(compact)
        self.assertTrue(repaired['passed'], repaired['issues'])

    async def test_science_motion_repair_prompt_keeps_the_same_validation_bar(self):
        failed = {'ran':True,'passed':False,'issues':['点击启动控件「开始」后，动画/模拟时间/作品内容未持续变化。']}
        passed = {'ran':True,'passed':True,'issues':[]}
        patch_json = json.dumps({'patches':[{'search':'<h1>种群模型</h1>','replace':'<h1>单摆</h1>'}]})
        request = normalize_interactive_request(RunPipelineV2Request(
            game_id='game', user_id='user', timeout_s=1800,
            raw_user_input='作品类型：科学演示。做一个单摆实验。',
            source_spec=GameSpec(game_type='interactive_experience', source_description='单摆')))
        with patch('src.engine.interactive_creation.LLMClient.complete_with_truncation_retry',
                   new=AsyncMock(side_effect=[GOOD, patch_json, await fake_llm(step_key='code_review')])) as llm, patch(
                'src.engine.interactive_creation.validate_interactive_html',
                new=AsyncMock(side_effect=[failed, passed])):
            await run_interactive(request)
        repair = llm.call_args_list[1].kwargs['messages'][0]['content']
        self.assertIn('非平衡', repair)
        self.assertIn('不要依赖拖拽', repair)
