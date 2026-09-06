import asyncio
import importlib.util
from pathlib import Path

path = Path(__file__).parents[1] / 'src/services/fc_runtime.py'
spec = importlib.util.spec_from_file_location('fc_runtime', path)
fc = importlib.util.module_from_spec(spec); spec.loader.exec_module(fc)


def test_transport_boundary_requires_token_and_preserves_health(monkeypatch):
    monkeypatch.setenv('FC_DEPLOYMENT', 'true')
    monkeypatch.setenv('FC_INTERNAL_TOKEN', 'a' * 64)
    calls = []
    async def app(scope, receive, send): calls.append(scope['path'])
    boundary = fc.FCTransportBoundary(app)
    async def invoke(path, headers=(), kind='http'):
        messages = []
        async def send(event): messages.append(event)
        await boundary({'type':kind, 'method':'GET', 'path':path, 'headers':headers}, None, send)
        return messages
    assert asyncio.run(invoke('/api/v1/ai/tasks'))[0]['status'] == 403
    assert asyncio.run(invoke('/ws',kind='websocket'))[0]['code'] == 1008
    assert asyncio.run(invoke('/api/v1/ai/tasks', [(b'x-fc-internal-token', b'a'*64)]))[0]['status'] == 403
    asyncio.run(invoke('/health'))
    asyncio.run(invoke('/api/v1/ai/tasks', [(b'x-gamevallies-internal-token', b'a'*64)]))
    assert calls == ['/health', '/api/v1/ai/tasks']


def test_relay_credentials_only_go_to_configured_service(monkeypatch):
    monkeypatch.setenv('FC_DEPLOYMENT', 'true')
    monkeypatch.setenv('FC_INTERNAL_TOKEN', 'a' * 64)
    monkeypatch.setenv('FC_INTERNAL_ORIGINS', 'https://game.example.com')
    assert fc.internal_headers('https://game.example.com/api')['x-gamevallies-internal-token'] == 'a'*64
    assert not any(k.startswith('x-fc-') for k in fc.internal_headers('https://game.example.com/api'))
    assert fc.internal_headers('https://game.example.com.evil.test/api') == {}
    assert fc.internal_headers('https://model.example.com') == {}
