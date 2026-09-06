"""Exercise a packaged Nginx server over HTTP, including master/worker startup."""
import subprocess
import time
import urllib.request


def verify_nginx_http(command, port):
    # command already passes syntax validation; remove only its final -t.
    assert command[-1] == '-t'
    detached = command[:2] + ['--detach', '-p', f'127.0.0.1::{port}'] + command[2:-1]
    container = subprocess.check_output(detached, text=True, timeout=30).strip()
    try:
        address = subprocess.check_output(
            ['docker', 'port', container, str(port)], text=True, timeout=10).strip()
        for _ in range(30):
            try:
                with urllib.request.urlopen('http://' + address + '/health', timeout=1) as response:
                    if response.status == 200 and response.read().strip() == b'ok':
                        print('Nginx HTTP health check passed', flush=True)
                        return
            except (OSError, ValueError):
                pass
            time.sleep(1)
        # These containers contain CI dummy values only, never cloud credentials.
        subprocess.run(['docker', 'logs', container], timeout=10, check=False)
        raise RuntimeError('Packaged Nginx did not serve /health')
    finally:
        subprocess.run(['docker', 'rm', '-f', container], timeout=15, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
