import pytest
from src.engine.interactive_creation import validate_interactive_html
from src.engine.interactive_contract_probes import rgb_output


@pytest.mark.parametrize('text,expected', [
    ('R 255 G 0 B 12', (255,0,12)), ('RGB: (255, 0, 12)', (255,0,12)),
    ('R 255 G 0 B 12 R 0 G 0 B 0', None), ('random 255,0,12', None),
])
def test_rgb_scope_is_explicit_and_unambiguous(text, expected):
    assert rgb_output(text) == expected


def colour_work(handler=''):
    return '''<!doctype html><html><body><h1>色板</h1><article>
    <input aria-label="HEX 值" value="4F7CFF" ''' + handler + '''>
    <output id="rgb">R 79 G 124 B 255</output></article>
    <button onclick="this.textContent='已随机'">随机</button>
    </body></html>'''


@pytest.mark.asyncio
async def test_dead_hex_input_cannot_hide_behind_working_random_button():
    report = await validate_interactive_html(colour_work())
    assert report['contentChanged']
    assert not report['passed']
    check = report['contractChecks'][0]
    assert check['status'] == 'failed'
    assert check['expected'] == [255,0,0]
    assert check['observed'] == [79,124,255]


@pytest.mark.asyncio
async def test_real_hex_rgb_conversion_passes_scoped_oracle():
    handler = '''oninput="if(/^[0-9a-f]{6}$/i.test(this.value))document.querySelector('#rgb').textContent=['R','G','B'].map((c,i)=>c+' '+parseInt(this.value.slice(i*2,i*2+2),16)).join(' ')"'''
    report = await validate_interactive_html(colour_work(handler))
    assert report['passed'], report['issues']
    assert report['contractChecks'][0]['status'] == 'passed'


def converter_work(*, live: bool, result_kind: str = 'input') -> str:
    result = (
        '<input id="result" readonly value="3.281">'
        if result_kind == 'input' else
        '<output id="result">3.281</output>'
    )
    handler = (
        'onclick="result.value ? result.value=String((Number(value.value)||0)*3.28084) : result.textContent=String((Number(value.value)||0)*3.28084)"'
        if live else ''
    )
    return f'''<!doctype html><html><body>
    <h1>米英尺换算</h1>
    <label>数值 <input id="value" type="number" value="1"></label>
    <button id="convert" {handler}>转换</button>
    {result}
    </body></html>'''


@pytest.mark.asyncio
async def test_unit_converter_result_input_counts_as_content_change():
    report = await validate_interactive_html(converter_work(live=True, result_kind='input'))
    assert report['contentChanged']
    assert not any('未检测到可操作且能改变作品内容的交互控件' in issue for issue in report['issues'])
    assert report['passed'], report['issues']


@pytest.mark.asyncio
async def test_unit_converter_output_node_still_passes():
    report = await validate_interactive_html(converter_work(live=True, result_kind='output'))
    assert report['passed'], report['issues']
    assert report['contentChanged']


@pytest.mark.asyncio
async def test_inert_converter_shell_still_fails_interactive_validation():
    report = await validate_interactive_html(converter_work(live=False, result_kind='input'))
    assert report['ran'] and not report['passed']
    assert any('未检测到可操作且能改变作品内容的交互控件' in issue for issue in report['issues'])


@pytest.mark.asyncio
async def test_unrelated_hex_identifier_is_not_misclassified_as_rgb_converter():
    html = colour_work().replace('HEX 值', '订单编号').replace('4F7CFF','123456')
    report = await validate_interactive_html(html)
    assert report['passed'], report['issues']
    assert report['contractChecks'] == []


@pytest.mark.asyncio
async def test_ambiguous_multi_card_output_is_unverified_not_certified():
    html = colour_work().replace('<article>', '<article><input aria-label="HEX 二" value="ABCDEF">')
    report = await validate_interactive_html(html)
    assert all(c['status'] == 'unverified' for c in report['contractChecks'])
    assert len(report['contractChecks']) == 2


@pytest.mark.asyncio
async def test_probe_control_detachment_is_not_soft_infrastructure_success():
    html = colour_work('oninput="if(this.value===\'FF0000\')this.remove()"')
    report = await validate_interactive_html(html)
    assert report['ran'] and not report['passed']
    assert report['contractChecks'][0]['status'] == 'failed'
    assert report['contractChecks'][0]['error'] == 'TimeoutError'
