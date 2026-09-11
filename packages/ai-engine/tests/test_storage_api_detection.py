import pytest

from src.engine.pipeline_v2_validation import PipelineV2ValidationMixin


@pytest.mark.parametrize('name', ['localStorage', 'sessionStorage'])
@pytest.mark.parametrize('source', [
    '<html><!-- no API used --><body>API is not used</body></html>',
    '<html><script>// no API used\nstart();</script></html>',
    '<html><script>/* API is disabled */ start();</script></html>',
    '<html><script>const example = /API/;</script></html>',
    '<html><script type="application/json">{"description":"API"}</script></html>',
])
def test_inert_storage_mentions_are_not_capability_usage(name, source):
    assert not PipelineV2ValidationMixin._contains_forbidden_api(source.replace('API', name), name)


@pytest.mark.parametrize('name', ['localStorage', 'sessionStorage'])
@pytest.mark.parametrize('source', [
    '<html><script>API.getItem("score");</script></html>',
    '<html><script>// comment\nwindow.API.setItem("score", 1);</script></html>',
    '<html><script>window["API"].getItem("score");</script></html>',
    '<html><script>const key="API"; window[key].clear();</script></html>',
    '<html><script>const s=`score: ${API.getItem("score")}`;</script></html>',
    '<html><script>window?.API?.clear();</script></html>',
    '<html><button onclick="API.clear()">clear</button></html>',
    '<html><a href="javascript:API.clear()">clear</a></html>',
    '<html><iframe srcdoc="&lt;script&gt;API.clear()&lt;/script&gt;"></iframe></html>',
    '<html><script>API.clear(); "unterminated</script></html>',
])
def test_real_storage_access_remains_blocked(name, source):
    assert PipelineV2ValidationMixin._contains_forbidden_api(source.replace('API', name), name)
