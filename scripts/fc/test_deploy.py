import copy
import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('fc_deploy', Path(__file__).with_name('deploy.py'))
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
from alibabacloud_fc20230330 import models as m

ENV = dict(FC_ACCOUNT_ID='123456789', FC_REGION='cn-shanghai', FC_PREFIX='gamevallies-test', ALIYUN_OSS_BUCKET='gamevallies-test', RELEASE_SHA='a'*40, FC_EXECUTION_ROLE='acs:ram::123456789:role/fc', FC_FRONTEND_URL='https://front.example.com')
RUNTIME = {'common': dict(DATABASE_URL='mysql://u:p@db:3306/db', REDIS_URL='redis://redis:6379', JWT_SECRET='jwt', JWT_REFRESH_SECRET='refresh', ADMIN_TOKEN='admin', FC_INTERNAL_TOKEN='a'*64), 'services': {'game-service': {'OBJECT_STORAGE_PROVIDER':'aliyun-oss','ALIYUN_OSS_ACCESS_KEY_ID':'id','ALIYUN_OSS_ACCESS_KEY_SECRET':'secret','ALIYUN_OSS_BUCKET':'gamevallies-test','ALIYUN_OSS_ENDPOINT':'https://oss-cn-shanghai.aliyuncs.com','ALIYUN_OSS_PREFIX':'gamevallies/prod/'}}, 'vpcConfig': {'vpcId':'vpc-123','vSwitchIds':['vsw-123'],'securityGroupId':'sg-123'}, 'nasConfig': {'mountPoints':[{'mountDir':'/mnt/data','serverAddr':'nas.internal:/data'}]}}
MANIFEST = json.loads(Path('deploy/fc/functions.json').read_text())

RUNTIME['artifacts'] = {f['name']: {'sha256': 'b'*64, 'object': 'gamevallies/prod/releases/' + 'a'*40 + '/' + 'b'*64 + '/' + f['name'] + '.zip'} for f in MANIFEST['functions']}

class ConfigTests(unittest.TestCase):
    def test_http_control_uses_header_forwarded_by_fc(self):
        import io
        response = Mock()
        response.__enter__ = Mock(return_value=io.BytesIO(b'{}'))
        response.__exit__ = Mock(return_value=False)
        with patch.object(d.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value = response
            d.http_json('https://example.com', 'test-token', '/__fc/status')
        request = opener.return_value.open.call_args.args[0]
        headers = {k.lower():v for k,v in request.header_items()}
        self.assertEqual(headers['x-gamevallies-internal-token'], 'test-token')
        self.assertFalse(any(k.startswith('x-fc-') for k in headers))

    def test_http_failure_reports_status_and_request_id_without_response_body(self):
        import io
        error = d.urllib.error.HTTPError('https://example.com', 502, 'secret',
            {'x-fc-request-id': 'request-123'}, io.BytesIO(b'private response'))
        output = io.StringIO()
        with patch('sys.stderr', output):
            d.report_error(error, 'http-health-check', 'content')
        self.assertIn('status=502', output.getvalue())
        self.assertIn('requestId=request-123', output.getvalue())
        self.assertNotIn('secret', output.getvalue())
        self.assertNotIn('private', output.getvalue())

    def test_restore_omits_empty_custom_runtime_handler_from_fc_response(self):
        client = Mock()
        deployment = d.Deployment(client, m)
        deployment.wait_function = Mock()
        for runtime in ('custom.debian12', 'custom-container'):
            previous = {'runtime': runtime, 'handler': '', 'description':
                'GameVallies OSS ' + json.dumps({'ossBucketName': 'bucket', 'ossObjectName': 'old.zip'})}
            deployment.restore_configuration('content', previous)
            self.assertNotIn('handler', client.update_function.call_args.args[1].body.to_map())
            self.assertEqual(previous['handler'], '')

    def test_reuse_requires_matching_code_and_configuration(self):
        client = Mock()
        current = m.Function(runtime='custom.debian12', code_checksum='abc',
                             environment_variables={'SECRET':'old'}, memory_size=1024)
        same = m.Function(runtime='custom.debian12', code_checksum='abc',
                          environment_variables={'SECRET':'old'}, memory_size=1024,
                          description='version description')
        client.get_function.return_value.body = same
        client.list_function_versions.return_value.body = NS(versions=[NS(version_id='7')], next_token=None)
        deployment = d.Deployment(client, m)
        self.assertEqual(deployment.matching_version('game', current), '7')
        same.environment_variables['SECRET'] = 'new'
        self.assertIsNone(deployment.matching_version('game', current))
        same.environment_variables['SECRET'] = 'old'
        same.code_checksum = 'other'
        self.assertIsNone(deployment.matching_version('game', current))

    def test_existing_version_avoids_publish_api(self):
        client = Mock()
        deployment = d.Deployment(client, m)
        deployment.matching_version = Mock(return_value='7')
        self.assertEqual(deployment.version('game'), '7')
        client.publish_function_version.assert_not_called()

    def test_unmatched_version_error_is_not_swallowed(self):
        client = Mock()
        deployment = d.Deployment(client, m)
        deployment.matching_version = Mock(return_value=None)
        error = RuntimeError('provider detail')
        error.code = 'VersionPublishError'
        client.publish_function_version.side_effect = error
        with self.assertRaises(RuntimeError) as caught:
            deployment.version('game')
        self.assertIs(caught.exception, error)

    def test_restore_uses_explicit_code_if_version_description_changed(self):
        client = Mock()
        client.get_function.return_value.body = m.Function(
            runtime='custom.debian12', description='version description')
        deployment = d.Deployment(client, m)
        deployment.wait_function = Mock()
        code = {'ossBucketName': 'old-bucket', 'ossObjectName': 'old.zip'}
        deployment.restore('game', '3', code)
        self.assertEqual(client.update_function.call_args.args[1].body.to_map()['code'], code)

    def test_stopped_existing_service_does_not_publish_checkpoint(self):
        import io
        client = Mock()
        code = {'ossBucketName': 'old-bucket', 'ossObjectName': 'old.zip'}
        old = NS(body=m.Function(runtime='custom.debian12',
                 description='GameVallies OSS ' + json.dumps(code), disable_ondemand=True))
        deployment = d.Deployment(client, m, lambda _: None)
        deployment.optional = Mock(side_effect=[None, old, old])
        deployment.service_is_stopped = Mock(return_value=True)
        deployment.trigger = Mock(return_value='https://example.com')
        deployment.migrate_database = Mock(side_effect=RuntimeError('stop before updates'))
        with patch('sys.stderr', io.StringIO()):
            with self.assertRaises(RuntimeError):
                deployment.apply({'functions':[MANIFEST['functions'][1]]}, RUNTIME, ENV, 'unused')
        client.publish_function_version.assert_not_called()
        client.update_function.assert_not_called()

    def test_skip_drain_requires_confirmed_zero_capacity(self):
        client = Mock()
        client.get_function.return_value.body = NS(disable_ondemand=True)
        p = NS(current=0, target=0, default_target=None,
               scheduled_actions=[], target_tracking_policies=[])
        client.get_provision_config.return_value.body = p
        deployment = d.Deployment(client, m)
        self.assertTrue(deployment.service_is_stopped('game'))
        for field, value in [('current', 1), ('current', None), ('target', 1),
                             ('target', None), ('default_target', 1),
                             ('scheduled_actions', [object()]),
                             ('target_tracking_policies', [object()])]:
            old = getattr(p, field)
            setattr(p, field, value)
            self.assertFalse(deployment.service_is_stopped('game'))
            setattr(p, field, old)
        client.get_function.return_value.body.disable_ondemand = False
        self.assertFalse(deployment.service_is_stopped('game'))

    def test_capacity_read_failure_is_not_ignored(self):
        client = Mock()
        client.get_function.return_value.body = NS(disable_ondemand=True)
        client.get_provision_config.side_effect = RuntimeError('unavailable')
        with self.assertRaises(RuntimeError):
            d.Deployment(client, m).service_is_stopped('game')

    def test_diagnostics_exclude_sdk_message_and_request_body(self):
        import io
        error = RuntimeError('mysql://secret password')
        error.code = 'PROVISIONING_FAILED'
        error.data = {'RequestId': 'request-123', 'body': 'secret'}
        output = io.StringIO()
        with patch('sys.stderr', output):
            d.report_error(error, 'provision-instances', 'ai-engine')
        self.assertIn('PROVISIONING_FAILED', output.getvalue())
        self.assertIn('ai-engine', output.getvalue())
        self.assertNotIn('secret', output.getvalue())

    def test_rollback_failure_preserves_original_failure(self):
        import io
        client = Mock()
        deployment = d.Deployment(client, m, sleep=lambda _: None)
        deployment.optional = Mock(return_value=None)
        deployment.wait_function = Mock()
        deployment.trigger = Mock(return_value='https://example.com')
        deployment.migrate_database = Mock()
        original = d.deployment_error('FUNCTION_UPDATE_FAILED', 'ai-engine')
        client.update_function.side_effect = [original, RuntimeError('rollback secret')]
        with patch('sys.stderr', io.StringIO()) as output:
            with self.assertRaises(RuntimeError) as caught:
                deployment.apply(MANIFEST, RUNTIME, ENV, 'unused.json')
        self.assertIs(caught.exception, original)
        self.assertIn('stage=update-function', output.getvalue())
        self.assertIn('stage=rollback', output.getvalue())
        self.assertNotIn('rollback secret', output.getvalue())

    def test_database_gate_uses_private_single_concurrency_executor(self):
        import io
        client = Mock()
        deployment = d.Deployment(client, m, sleep=lambda _: None)
        deployment.optional = Mock(return_value=None)
        deployment.wait_function = Mock()
        client.invoke_function_with_options.return_value = NS(status_code=200, body=io.BytesIO(b'{"ok":true,"stage":"database-ready"}'))
        deployment.migrate_database(MANIFEST, RUNTIME, ENV)
        body = client.create_function.call_args.args[0].body.to_map()
        self.assertEqual(body['instanceConcurrency'], 1)
        self.assertFalse(body['internetAccess'])
        self.assertEqual(body['vpcConfig'], RUNTIME['vpcConfig'])
        self.assertEqual(body['code']['ossObjectName'], RUNTIME['artifacts']['game-service']['object'])
        self.assertEqual(body['environmentVariables']['DATABASE_URL'], RUNTIME['common']['DATABASE_URL'])
        self.assertNotIn('JWT_SECRET', body['environmentVariables'])
        self.assertNotIn('NODE_OPTIONS', body['environmentVariables'])
        client.create_trigger.assert_not_called()
        client.put_provision_config.assert_not_called()
        self.assertTrue(client.update_function.call_args.args[1].body.disable_ondemand)

    def test_database_failure_blocks_application_publication(self):
        client = Mock()
        deployment = d.Deployment(client, m, sleep=lambda _: None)
        deployment.optional = Mock(return_value=None)
        deployment.migrate_database = Mock(side_effect=RuntimeError('database rejected'))
        deployment.wait_function = Mock()
        deployment.trigger = Mock(return_value='https://example.com')
        with self.assertRaises(RuntimeError):
            deployment.apply(MANIFEST, RUNTIME, ENV, 'unused.json')
        for call in client.create_function.call_args_list:
            self.assertTrue(call.args[0].body.disable_ondemand)
        client.update_function.assert_not_called()
        client.put_provision_config.assert_not_called()
        client.publish_function_version.assert_not_called()

    def test_database_error_is_safe_and_executor_is_disabled(self):
        import io
        client = Mock()
        deployment = d.Deployment(client, m, sleep=lambda _: None)
        deployment.optional = Mock(return_value=None)
        deployment.wait_function = Mock()
        client.invoke_function_with_options.return_value = NS(status_code=200, body=io.BytesIO(b'{"ok":false,"code":"P3009"}'))
        with self.assertRaises(RuntimeError) as caught:
            deployment.migrate_database(MANIFEST, RUNTIME, ENV)
        self.assertEqual(caught.exception.code, 'P3009')
        self.assertTrue(client.update_function.call_args.args[1].body.disable_ondemand)

    def test_official_sdk_round_trip_preserves_runtime_and_port(self):
        d.validate(MANIFEST, RUNTIME, ENV)
        for f in MANIFEST['functions']:
            body = d.function_body(f,RUNTIME,ENV,{'ai-engine':'https://ai.example.com','game-service':'https://game.example.com'})
            model=m.CreateFunctionInput().from_map(body); model.validate()
            self.assertEqual(model.to_map()['customRuntimeConfig']['port'],f['port'])
            update=m.UpdateFunctionInput().from_map(body).to_map()
            self.assertNotIn('functionName', update)
            if f.get('background'): self.assertTrue(update['disableOndemand'])
    def test_fc_reserved_names_are_not_sent_to_api(self):
        for f in MANIFEST['functions']:
            values = d.function_body(f, RUNTIME, ENV, {})['environmentVariables']
            self.assertFalse(any(k.startswith('FC_') for k in values))
            self.assertEqual(values['GAMEVALLIES_FC_INTERNAL_TOKEN'], RUNTIME['common']['FC_INTERNAL_TOKEN'])

    def test_consolidated_backend_needs_no_frontend_url(self):
        env = {k: v for k, v in ENV.items() if k != 'FC_FRONTEND_URL'}
        d.validate(MANIFEST, RUNTIME, env)
        content = next(f for f in MANIFEST['functions'] if f['name'] == 'content')
        body = d.function_body(content, RUNTIME, env, {'game-service': 'https://api.example.com'})
        self.assertEqual(body['environmentVariables']['GAME_UPSTREAM'], 'https://api.example.com')

    def test_rejects_missing_persistence_and_frozen_workers(self):
        if not any(f.get('background') for f in MANIFEST['functions']): return
        bad=copy.deepcopy(MANIFEST)
        next(f for f in bad['functions'] if f.get('background'))['provisioned']=0
        with self.assertRaises(ValueError): d.validate(bad,RUNTIME,ENV)
        bad=copy.deepcopy(RUNTIME); bad['services']['game-service']['OBJECT_STORAGE_PROVIDER']='local'
        with self.assertRaises(ValueError): d.validate(MANIFEST,bad,ENV)
    def test_rejects_mutable_tags_and_credential_urls(self):
        with self.assertRaises(ValueError): d.validate(MANIFEST,RUNTIME,{**ENV,'RELEASE_SHA':'latest'})
        with self.assertRaises(ValueError): d.origin('https://user:secret@example.com')
    def test_provision_waits_for_transient_error_to_clear(self):
        client = Mock()
        client.get_provision_config.side_effect = [
            NS(body=NS(current=0, target=1, always_allocate_cpu=True, current_error='old startup error')),
            NS(body=NS(current=1, target=1, always_allocate_cpu=True, current_error='old startup error')),
            NS(body=NS(current=1, target=1, always_allocate_cpu=True, current_error=None)),
        ]
        sleep = Mock()
        d.Deployment(client, m, sleep).provision('worker', 1)
        self.assertEqual(sleep.call_count, 2)

    def test_provision_timeout_with_no_error_is_distinct(self):
        client = Mock()
        client.get_provision_config.return_value.body = NS(current=0, target=1, always_allocate_cpu=True, current_error=None)
        with self.assertRaises(RuntimeError) as caught:
            d.Deployment(client, m, lambda _: None).provision('worker', 1)
        self.assertEqual(caught.exception.code, 'PROVISIONING_TIMEOUT')
        self.assertEqual(client.get_provision_config.call_count, 90)

    def test_provision_checks_actual_cpu_and_count(self):
        client=Mock();client.get_provision_config.return_value.body=NS(current=1,target=1,always_allocate_cpu=True,current_error=None)
        deployment=d.Deployment(client,m,sleep=lambda _:None); deployment.provision('worker',1)
        body=client.put_provision_config.call_args.args[1].body.to_map()
        self.assertTrue(body['alwaysAllocateCPU']);self.assertEqual(body['defaultTarget'],1)
        client.get_provision_config.return_value.body.current_error='capacity unavailable'
        with self.assertRaises(RuntimeError): deployment.provision('worker',1)
    def test_restore_uses_selected_version_not_latest(self):
        client=Mock();client.get_function.return_value.body=m.Function(runtime='custom-container',cpu=1,memory_size=1024,custom_container_config=m.CustomContainerConfig(image='registry/project/image:old',port=8080),state='Active',last_update_status='Successful')
        deployment=d.Deployment(client,m,sleep=lambda _:None); deployment.restore('front','17')
        self.assertEqual(client.get_function.call_args_list[0].args[1].qualifier,'17')
        self.assertEqual(client.update_function.call_args.args[1].body.custom_container_config.image,'registry/project/image:old')
    def test_restore_code_uses_checkpoint_oss_reference(self):
        code={'ossBucketName':'bucket-old','ossObjectName':'release/old.zip'}
        client=Mock(); client.get_function.return_value.body=m.Function(runtime='custom.debian12', description='GameVallies OSS '+json.dumps(code), state='Active', last_update_status='Successful')
        deployment=d.Deployment(client,m,sleep=lambda _:None); deployment.restore('web','17')
        body=client.update_function.call_args.args[1].body.to_map()
        self.assertEqual(body['code'],code)
        self.assertNotIn('customContainerConfig',body)
    def test_rejects_unknown_code_checkpoint_and_mismatched_artifacts(self):
        with self.assertRaises(ValueError): d.checkpoint_code({'description':'manual code deployment'})
        bad=copy.deepcopy(RUNTIME); bad['artifacts'][MANIFEST['functions'][0]['name']]['object']='mutable/latest.zip'
        with self.assertRaises(ValueError): d.validate(MANIFEST,bad,ENV)
    def test_permission_error_is_not_treated_as_missing_function(self):
        error=Exception('forbidden');error.status_code=403
        with self.assertRaises(Exception): d.Deployment(Mock(),m).optional(Mock(side_effect=error))
    def test_zip_without_async_state_requires_persisted_code(self):
        pending = NS(state=None, last_update_status=None, runtime='custom.debian12', code_checksum=None, code_size=0)
        ready = NS(state=None, last_update_status=None, runtime='custom.debian12', code_checksum='checksum', code_size=123)
        client = Mock()
        client.get_function.side_effect = [NS(body=pending), NS(body=ready)]
        sleep = Mock()
        self.assertIs(d.Deployment(client, m, sleep).wait_function('zip'), ready)
        sleep.assert_called_once()

    def test_explicit_failure_is_never_accepted(self):
        client = Mock()
        client.get_function.return_value.body = NS(state='Failed', last_update_status=None)
        with self.assertRaises(RuntimeError):
            d.Deployment(client, m, Mock()).wait_function('failed')

    def test_function_waits_for_completed_update(self):
        client=Mock(); client.get_function.side_effect=[NS(body=NS(state='Active',last_update_status='InProgress')),NS(body=NS(state='Active',last_update_status='Successful'))]
        sleep=Mock(); d.Deployment(client,m,sleep).wait_function('x');sleep.assert_called_once()

if __name__=='__main__': unittest.main()
