import pytest
from src.engine.interactive_browser_qa import (
    classify_control_action, motion_control_label, parameter_stimulus_priority,
    plan_motion_parameter_stimuli)
from src.engine.interactive_creation import (
    interactive_system_prompt, science_runtime_repair_guidance, validate_interactive_html)


@pytest.mark.parametrize('kwargs,expected', [
    ({'text': '开始演示'}, 'motion'),
    ({'text': '▶', 'aria_label': '开始'}, 'motion'),
    ({'text': '▶', 'control_id': 'start'}, 'motion'),
    ({'text': '暂停'}, 'stop'),
    ({'text': '▶', 'aria_label': '暂停'}, 'stop'),
    ({'text': '设置'}, 'other'),
    ({'text': '取消', 'aria_label': '开始'}, 'stop'),
])
def test_control_action_uses_accessible_name_without_guessing(kwargs, expected):
    assert classify_control_action(**kwargs) == expected


def test_icon_start_reports_accessible_motion_label():
    assert motion_control_label(text='▶', aria_label='开始') == '开始'


def test_displacement_parameters_are_tried_before_length_or_gravity():
    assert parameter_stimulus_priority('初角') < parameter_stimulus_priority('L')
    assert parameter_stimulus_priority('amplitude') < parameter_stimulus_priority('g')
    planned = plan_motion_parameter_stimuli([
        {'kind': 'range', 'index': 0, 'name': 'L'},
        {'kind': 'range', 'index': 1, 'name': 'g'},
        {'kind': 'number', 'index': 0, 'name': 'theta0'},
        {'kind': 'number', 'index': 1, 'name': 'unused', 'enabled': False},
    ])
    assert [item['name'] for item in planned] == ['theta0', 'L', 'g']


def test_science_contract_is_not_applied_to_tools_and_does_not_relax_qa():
    assert '非平衡' in interactive_system_prompt('science')
    assert '不要把拖拽' in interactive_system_prompt('science')
    assert '非平衡' not in interactive_system_prompt('tool')
    assert '验收不会放宽' in interactive_system_prompt('science')


def test_science_repair_guidance_is_tied_to_observed_runtime_defects():
    motion = science_runtime_repair_guidance(['点击启动控件「开始」后，动画/模拟时间/作品内容未持续变化。'])
    assert '非平衡' in motion and 'θ=0' in motion
    layout = science_runtime_repair_guidance(['1000×600 (visible) 核心图形/控件不完整：start。'])
    assert '1000×600' in layout
    assert science_runtime_repair_guidance(['未检测到可操作且能改变作品内容的交互控件。']) == ''


def tabbed_work(working=True):
    start = 'setInterval(()=>count.textContent=String(++ticks),100)' if working else 'count.textContent="1"'
    return '''<!doctype html><html><body><h1>节拍工具</h1>
    <div role="tablist">
      <button role="tab" id="measure" onclick="demo.hidden=true;measurement.hidden=false">测量模式</button>
      <button role="tab" id="mode" onclick="demo.hidden=false;measurement.hidden=true">▶ 节拍演示</button>
    </div>
    <section id="measurement"><button id="tap" onclick="this.textContent='已记录'">拍点</button></section>
    <section id="demo" hidden>
      <button id="preset" onclick="this.textContent='已设置'">预设</button>
      <button id="start" onclick="''' + start + '''">开始演示</button>
      <button id="reset" onclick="count.textContent='0'">重置演示</button>
      <output id="count">0</output>
    </section><script>let ticks=0;function unused(){setInterval(()=>{},100)}</script>
    </body></html>'''


@pytest.mark.asyncio
async def test_navigation_reveals_real_start_and_is_restored_after_other_buttons():
    report = await validate_interactive_html(tabbed_work())
    assert report['ran'] and report['passed'], report['issues']
    assert {c['control'] for c in report['controlChecks']} >= {'mode', 'tap', 'preset', 'start', 'reset'}
    assert [c['control'] for c in report['motionChecks']] == ['开始演示']
    assert report['motionChecks'][0]['advances']


@pytest.mark.asyncio
async def test_dead_start_behind_a_working_tab_still_fails():
    report = await validate_interactive_html(tabbed_work(working=False))
    assert report['ran'] and not report['passed']
    assert report['motionChecks'][0]['control'] == '开始演示'
    assert not report['motionChecks'][0]['advances']
    assert all('▶ 节拍演示' not in issue for issue in report['issues'])
