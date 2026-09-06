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
