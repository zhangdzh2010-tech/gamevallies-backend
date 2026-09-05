"""Exercise release switching and rollback without touching a Docker daemon."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('deploy.sh')
OLD = 'a' * 40
NEW = 'b' * 40

class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.deploy = self.base / 'deployment'
        self.deploy.mkdir()
        (self.deploy / 'runtime.env').write_text('\n'.join(f'{k}=test-value' for k in (
            'DATABASE_URL', 'REDIS_URL', 'JWT_SECRET', 'JWT_REFRESH_SECRET', 'ADMIN_TOKEN')))
        binary = self.base / 'bin'
        binary.mkdir()
        mock = binary / 'docker'
        mock.write_text('''#!/usr/bin/env python3
import os, pathlib, sys
args=sys.argv[1:]
if args[0]=='compose':
    manifest=pathlib.Path(args[args.index('--env-file')+1])
    values=dict(line.split('=',1) for line in manifest.read_text().splitlines())
    tag=values['IMAGE_TAG']
    with open(os.environ['DOCKER_TEST_LOG'],'a') as f:
        f.write(tag+' '+' '.join(args[args.index('-f')+2:])+'\\n')
    if tag==os.environ.get('FAIL_TAG') and os.environ.get('FAIL_COMMAND','up') in args:
        sys.exit(1)
sys.exit(0)
''')
        mock.chmod(0o755)
        self.log = self.base / 'docker.log'
        self.env = {**os.environ, 'PATH': str(binary)+os.pathsep+os.environ['PATH'],
                    'DEPLOY_ROOT': str(self.deploy), 'IMAGE_PREFIX': 'registry.example.com/gamevallies',
                    'IMAGE_TAG': OLD, 'DOCKER_TEST_LOG': str(self.log)}

    def run_release(self, **extra):
        return subprocess.run(['bash', str(SCRIPT)], env={**self.env, **extra}, capture_output=True, text=True)

    def test_success_switches_release(self):
        result = self.run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        current = (self.deploy/'current').resolve()
        self.assertIn(f'IMAGE_TAG={OLD}', (current/'images.env').read_text())
        self.assertIn('up -d --wait --wait-timeout 240', self.log.read_text())

    def test_unhealthy_update_restores_previous_image_tag(self):
        self.assertEqual(self.run_release().returncode, 0)
        before = (self.deploy/'current').resolve()
        result = self.run_release(IMAGE_TAG=NEW, FAIL_TAG=NEW)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.deploy/'current').resolve(), before)
        lines = self.log.read_text().splitlines()
        self.assertTrue(lines[-2].startswith(NEW+' up '))
        self.assertTrue(lines[-1].startswith(OLD+' up '))
        self.assertIn('Previous release restored', result.stderr)

    def test_pull_failure_does_not_restart_running_release(self):
        self.assertEqual(self.run_release().returncode, 0)
        result = self.run_release(IMAGE_TAG=NEW, FAIL_TAG=NEW, FAIL_COMMAND='pull')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.log.read_text().splitlines()[-1].startswith(NEW+' pull'))
        self.assertIn(OLD, str((self.deploy/'current').resolve()))

    def test_first_release_failure_does_not_publish_current(self):
        result = self.run_release(FAIL_TAG=OLD)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.deploy/'current').exists())
        self.assertIn('No previous release', result.stderr)

    def test_invalid_tag_is_rejected_before_docker(self):
        self.assertNotEqual(self.run_release(IMAGE_TAG='latest').returncode, 0)
        self.assertFalse(self.log.exists())

    def test_failed_rollback_is_reported(self):
        self.assertEqual(self.run_release().returncode, 0)
        # Force the previous compose manifest to pull the same failing tag too.
        previous = (self.deploy/'current').resolve()/'images.env'
        previous.write_text(previous.read_text().replace(OLD, NEW))
        result = self.run_release(IMAGE_TAG=NEW, FAIL_TAG=NEW)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ROLLBACK FAILED', result.stderr)

if __name__ == '__main__':
    unittest.main()
