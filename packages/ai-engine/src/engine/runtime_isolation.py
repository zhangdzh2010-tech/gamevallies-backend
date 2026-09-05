"""Browser-level network restrictions; deployment must also isolate QA workers."""
from __future__ import annotations

# Inline code and embedded assets are needed by self-contained generated games.
# connect-src covers WebSocket/WebTransport as well as fetch/XHR.
NETWORK_CSP = (
    "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' data: blob:; "
    "style-src 'unsafe-inline'; img-src data: blob:; media-src data: blob:; "
    "font-src data:; connect-src 'none'; worker-src 'none'; frame-src 'none'; "
    "object-src 'none'; base-uri 'none'; form-action 'none'"
)


async def restrict_context_network(context) -> None:
    async def deny_network(route):
        await route.abort("blockedbyclient")

    # Installed before any page/content. Also covers popups and redirects.
    await context.route("**/*", deny_network)


def network_policy_meta() -> str:
    return f'<meta http-equiv="Content-Security-Policy" content="{NETWORK_CSP}">'
