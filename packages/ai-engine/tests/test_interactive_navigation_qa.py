import pytest
from src.engine.interactive_creation import validate_interactive_html


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
