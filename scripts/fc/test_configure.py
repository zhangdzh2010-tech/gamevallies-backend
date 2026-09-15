import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import zipfile
import configure as c
from deploy import filter_manifest, parse_services

class PackageConfigTests(unittest.TestCase):
    def test_backend_log_configuration_keeps_metrics_enabled(self):
        env = dict(FC_REGION='cn-hongkong', ALIYUN_OSS_REGION='cn-hongkong',
            ALIYUN_OSS_ENDPOINT='https://oss-cn-hongkong.aliyuncs.com',
            CONTENT_ORIGIN='https://content.example.com', FC_LOG_PROJECT='test-project',
            FC_LOG_STORE='fc-runtime')
        with patch.object(c, 'need', side_effect=lambda values, key: values.get(key, 'test')):
            config = c.runtime_config(env, True)['logConfig']
        self.assertEqual(config, {'project': 'test-project', 'logstore': 'fc-runtime',
            'enableInstanceMetrics': True, 'enableRequestMetrics': True,
            'logBeginRule': 'DefaultRegex'})

    def test_nginx_strips_fc_attachment_from_browser_html_routes(self):
        import re
        def blocks(path):
            text = (Path(__file__).resolve().parents[2] / path).read_text(encoding='utf-8')
            return re.findall(r'location[^{]+\{[^}]+\}', text)
        def block_for(items, prefix):
            matches = [b for b in items if b.split('{', 1)[0].strip().endswith(prefix)]
            self.assertTrue(matches, prefix)
            return matches[0]
        app = blocks('deploy/fc/nginx-app.conf.template')
        content = blocks('deploy/fc/nginx-content.conf.template')
        for prefix in (' /admin', ' /games/', ' /game-shell/'):
            self.assertIn('proxy_hide_header Content-Disposition', block_for(app, prefix))
        self.assertNotIn('proxy_hide_header Content-Disposition', block_for(app, '/api/v1/growth'))
        for prefix in (' /games/', ' /game-shell/'):
            self.assertIn('proxy_hide_header Content-Disposition', block_for(content, prefix))

    def test_rejects_missing_settings_together_without_values(self):
        with self.assertRaisesRegex(ValueError, 'FC_ACCOUNT_ID.*FC_REGION.*FC_PREFIX'):
            c.check_settings({}, {'functions': [{'name':'frontend'}]})

    def test_requires_separate_content_origin(self):
        with self.assertRaisesRegex(ValueError, 'separate'):
            c.runtime_config({'PUBLIC_ORIGIN':'https://app.example.com', 'CONTENT_ORIGIN':'https://app.example.com'}, False)

    def test_payment_credentials_are_scoped_to_consolidated_api(self):
        env = dict(PUBLIC_ORIGIN='https://www.example.com', CONTENT_ORIGIN='https://content.example.com',
            FC_REGION='configured', ALIYUN_OSS_REGION='configured',
            ALIYUN_OSS_ENDPOINT='https://oss-configured.aliyuncs.com',
            WECHAT_PAY_MODE='real', ALIPAY_MODE='real')
        required = {
            'WECHAT_MINIAPP_APP_ID', 'WECHAT_MINIAPP_APP_SECRET', 'WECHAT_H5_APP_ID',
            'WECHAT_H5_APP_SECRET', 'WECHAT_H5_OAUTH_SCOPE', 'WECHAT_PAY_MODE',
            'WECHAT_PAY_MERCHANT_ID', 'WECHAT_PAY_NOTIFY_URL', 'WECHAT_PAY_SERIAL_NO',
            'WECHAT_PAY_PRIVATE_KEY', 'WECHAT_PAY_PUBLIC_KEY', 'WECHAT_PAY_API_V3_KEY',
            'WECHAT_PAY_API_BASE', 'ALIPAY_MODE', 'ALIPAY_APP_ID', 'ALIPAY_PRIVATE_KEY',
            'ALIPAY_PUBLIC_KEY', 'ALIPAY_NOTIFY_URL', 'ALIPAY_GATEWAY', 'ALIPAY_SIGN_TYPE',
        }
        with patch.object(c, 'need', side_effect=lambda values, key: values.get(key, 'configured')):
            runtime = c.runtime_config(env, True)
        game = runtime['services']['game-service']
        self.assertTrue(required.issubset(game))
        self.assertEqual(runtime['common']['PUBLIC_WEB_BASE_URL'], 'https://www.example.com')

    def test_cors_allowlist_includes_www_and_apex(self):
        env = dict(PUBLIC_ORIGIN='https://www.zlspace.ai', CONTENT_ORIGIN='https://content.zlspace.ai',
            FC_REGION='cn-shenzhen', ALIYUN_OSS_REGION='cn-shenzhen',
            ALIYUN_OSS_ENDPOINT='https://oss-cn-shenzhen.aliyuncs.com')
        with patch.object(c, 'need', side_effect=lambda values, key: values.get(key, 'configured')):
            runtime = c.runtime_config(env, True)
        common = runtime['common']
        self.assertEqual(common['CORS_ORIGIN'], 'https://www.zlspace.ai,https://zlspace.ai')
        self.assertEqual(json.loads(common['CORS_ORIGINS']),
                         ['https://www.zlspace.ai', 'https://zlspace.ai'])
        self.assertEqual(common['FRONTEND_URL'], 'https://www.zlspace.ai')
        self.assertEqual(common['PUBLIC_WEB_BASE_URL'], 'https://www.zlspace.ai')
        self.assertEqual(c.site_cors_origins('https://zlspace.ai'),
                         ['https://zlspace.ai', 'https://www.zlspace.ai'])
        self.assertEqual(c.site_cors_origins('https://app.example.com'), ['https://app.example.com'])

    def test_zip_bootstrap_digest_and_path_guards(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'frontend.zip'
            def write(mode=0o755, extra=None):
                with zipfile.ZipFile(path,'w') as archive:
                    info=zipfile.ZipInfo('bootstrap'); info.external_attr=(0o100000|mode)<<16
                    archive.writestr(info,'#!/bin/sh\nexit 0\n')
                    if extra: archive.writestr(extra,'bad')
            write()
            result=c.package_index(folder,{'functions':[{'name':'frontend'}]},{'RELEASE_SHA':'a'*40})
            self.assertEqual(len(result['frontend']['sha256']),64)
            self.assertIn('/releases/'+'a'*40+'/', result['frontend']['object'])
            write(mode=0o644)
            with self.assertRaisesRegex(ValueError,'executable'): c.package_index(folder,{'functions':[{'name':'frontend'}]},{'RELEASE_SHA':'a'*40})
            for entry in ('../outside', '.env.production', '/absolute'):
                write(extra=entry)
                with self.assertRaisesRegex(ValueError,'Unsafe'): c.package_index(folder,{'functions':[{'name':'frontend'}]},{'RELEASE_SHA':'a'*40})

    def test_package_index_follows_filtered_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'game-service.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                info = zipfile.ZipInfo('bootstrap')
                info.external_attr = (0o100000 | 0o755) << 16
                archive.writestr(info, '#!/bin/sh\nexit 0\n')
            catalog = json.loads(Path('deploy/fc/functions.json').read_text())
            selected = filter_manifest(catalog, 'game-service')
            result = c.package_index(folder, selected, {'RELEASE_SHA': 'a' * 40})
            self.assertEqual(list(result), ['game-service'])
            self.assertTrue(result['game-service']['object'].endswith('/game-service.zip'))
            with self.assertRaises(FileNotFoundError):
                c.package_index(folder, catalog, {'RELEASE_SHA': 'a' * 40})

    def test_build_script_lists_selected_targets_without_docker(self):
        env = {**os.environ, 'FC_SERVICES': 'content, ai-engine', 'FC_BUILD_LIST_ONLY': '1'}
        listed = subprocess.check_output(['bash', 'scripts/fc/build.sh'], env=env, text=True)
        self.assertEqual(listed.splitlines(), ['ai-engine', 'content'])
        self.assertEqual(parse_services('content, ai-engine'), ('ai-engine', 'content'))
        failed = subprocess.run(
            ['bash', 'scripts/fc/build.sh', 'frontend'],
            env={**os.environ, 'FC_BUILD_LIST_ONLY': '1'},
            capture_output=True, text=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('Unknown FC service', failed.stderr)

    def test_upload_rejects_changed_package_before_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'frontend.zip').write_bytes(b'changed')
            env=dict(ALIBABA_CLOUD_ACCESS_KEY_ID='test',ALIBABA_CLOUD_ACCESS_KEY_SECRET='test',FC_REGION='cn-shenzhen',ALIYUN_OSS_BUCKET='test-bucket')
            with patch('oss2.Bucket') as bucket:
                with self.assertRaisesRegex(ValueError,'changed'):
                    c.upload(folder, {'frontend':{'sha256':'0'*64,'object':'test'}},env)
                bucket.return_value.put_object_from_file.assert_not_called()

if __name__=='__main__': unittest.main()
