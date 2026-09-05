import asyncio

from playwright.async_api import async_playwright

from src.engine.runtime_isolation import network_policy_meta, restrict_context_network


def test_browser_executes_inline_game_but_blocks_network_and_frames():
    async def check():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                context = await browser.new_context(service_workers="block")
                await restrict_context_network(context)
                page = await context.new_page()
                await page.set_content(network_policy_meta() + """
                    <canvas id="game" width="100" height="100"></canvas>
                    <script>
                    window.score = 0;
                    document.getElementById('game').onclick = () => window.score++;
                    </script>
                """)
                await page.click("#game")
                assert await page.evaluate("window.score") == 1
                for url in ("http://127.0.0.1:8765/private", "http://169.254.169.254/latest/meta-data/",
                            "https://example.com/track"):
                    assert await page.evaluate("url => fetch(url).then(() => true, () => false)", url) is False
                # Even without the HTML CSP, the context-level request boundary blocks I/O.
                other = await context.new_page()
                await other.set_content("<p>no policy</p>")
                assert await other.evaluate(
                    "() => fetch('https://example.com/track').then(() => true, () => false)"
                ) is False
                assert await page.evaluate("""() => new Promise(resolve => {
                    document.addEventListener('securitypolicyviolation', event => {
                        if (event.blockedURI.startsWith('ws:') && event.effectiveDirective === 'connect-src') resolve(true);
                    }, {once: true});
                    try { new WebSocket('ws://127.0.0.1:8765/socket'); }
                    catch (e) { resolve(e.name === 'SecurityError'); }
                    setTimeout(() => resolve(false), 1500);
                })""") is True
            finally:
                await browser.close()
    asyncio.run(check())


def test_sandboxed_game_cannot_read_host_document():
    async def check():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = await browser.new_page()
                await page.set_content('''<iframe sandbox="allow-scripts" srcdoc="<p>game</p>"></iframe>''')
                frame = page.frames[1]
                assert await frame.evaluate("""() => {
                    try { parent.document.body.innerHTML; return false; }
                    catch (e) { return e.name === 'SecurityError'; }
                }""") is True
            finally:
                await browser.close()
    asyncio.run(check())
