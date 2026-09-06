import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import zipfile
import configure as c

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

    def test_rejects_missing_settings_together_without_values(self):
        with self.assertRaisesRegex(ValueError, 'FC_ACCOUNT_ID.*FC_REGION.*FC_PREFIX'):
            c.check_settings({}, {'functions': [{'name':'frontend'}]})

    def test_requires_separate_content_origin(self):
        with self.assertRaisesRegex(ValueError, 'separate'):
            c.runtime_config({'PUBLIC_ORIGIN':'https://app.example.com', 'CONTENT_ORIGIN':'https://app.example.com'}, False)

    def test_payment_credentials_are_scoped_to_consolidated_api(self):
        env = dict(PUBLIC_ORIGIN='https://www.example.com', CONTENT_ORIGIN='https://content.example.com',
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

    def test_upload_rejects_changed_package_before_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'frontend.zip').write_bytes(b'changed')
            env=dict(ALIBABA_CLOUD_ACCESS_KEY_ID='test',ALIBABA_CLOUD_ACCESS_KEY_SECRET='test',FC_REGION='cn-shenzhen',ALIYUN_OSS_BUCKET='test-bucket')
            with patch('oss2.Bucket') as bucket:
                with self.assertRaisesRegex(ValueError,'changed'):
                    c.upload(folder, {'frontend':{'sha256':'0'*64,'object':'test'}},env)
                bucket.return_value.put_object_from_file.assert_not_called()

if __name__=='__main__': unittest.main()
