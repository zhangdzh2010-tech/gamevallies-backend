"""FC transport boundary; domain authorization remains in the existing handlers."""
import hmac
import os
import re
from urllib.parse import urlsplit


def internal_headers(url: str) -> dict[str, str]:
    if os.getenv('FC_DEPLOYMENT') != 'true':
        return {}
    parsed = urlsplit(url)
    origin = f'{parsed.scheme}://{parsed.netloc}'
    allowed = os.getenv('FC_INTERNAL_ORIGINS', '').split(',')
    return {'x-gamevallies-internal-token': os.environ['FC_INTERNAL_TOKEN']} if origin in allowed else {}


class FCTransportBoundary:
    def __init__(self, app):
        self.app = app
        self.enabled = os.getenv('FC_DEPLOYMENT') == 'true'
        self.token = os.getenv('FC_INTERNAL_TOKEN', '')
        if self.enabled and not re.fullmatch(r'[a-f0-9]{64}', self.token):
            raise RuntimeError('FC_INTERNAL_TOKEN must be 32 random bytes encoded as hex')

    async def __call__(self, scope, receive, send):
        if self.enabled and scope['type'] in ('http', 'websocket'):
            health = scope['type'] == 'http' and scope.get('method') == 'GET' and scope.get('path') == '/health'
            supplied = dict(scope.get('headers', [])).get(b'x-gamevallies-internal-token', b'')
            if not health and not hmac.compare_digest(supplied, self.token.encode()):
                if scope['type'] == 'websocket':
                    await send({'type': 'websocket.close', 'code': 1008})
                else:
                    await send({'type': 'http.response.start', 'status': 403, 'headers': []})
                    await send({'type': 'http.response.body', 'body': b'Forbidden'})
                return
        await self.app(scope, receive, send)
