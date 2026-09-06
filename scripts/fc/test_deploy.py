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

ENV = dict(FC_ACCOUNT_ID='123456789', FC_REGION='cn-shanghai', FC_PREFIX='gamevallies-test', ACR_REGISTRY='registry.cn-shanghai.aliyuncs.com', ACR_NAMESPACE='gamevallies', IMAGE_TAG='a'*40, FC_EXECUTION_ROLE='acs:ram::123456789:role/fc', FC_FRONTEND_URL='https://front.example.com')
RUNTIME = {'common': dict(DATABASE_URL='mysql://u:p@db:3306/db', REDIS_URL='redis://redis:6379', JWT_SECRET='jwt', JWT_REFRESH_SECRET='refresh', ADMIN_TOKEN='admin', FC_INTERNAL_TOKEN='a'*64), 'services': {'game-service': {'APP_RELEASE_UPLOAD_DIR':'/mnt/data/releases'}}, 'vpcConfig': {'vpcId':'vpc-123','vSwitchIds':['vsw-123'],'securityGroupId':'sg-123'}, 'nasConfig': {'mountPoints':[{'mountDir':'/mnt/data','serverAddr':'nas.internal:/data'}]}}
MANIFEST = json.loads(Path('deploy/fc/functions.json').read_text())

class ConfigTests(unittest.TestCase):
    def test_official_sdk_round_trip_preserves_runtime_and_port(self):
        d.validate(MANIFEST, RUNTIME, ENV)
        for f in MANIFEST['functions']:
            body = d.function_body(f,RUNTIME,ENV,{'ai-engine':'https://ai.example.com','game-service':'https://game.example.com'})
            model=m.CreateFunctionInput().from_map(body); model.validate()
            self.assertEqual(model.to_map()['customContainerConfig']['port'],f['port'])
            update=m.UpdateFunctionInput().from_map(body).to_map()
            self.assertNotIn('functionName', update)
            if f.get('background'): self.assertTrue(update['disableOndemand'])
    def test_rejects_missing_persistence_and_frozen_workers(self):
        if not any(f.get('background') for f in MANIFEST['functions']): return
        bad=copy.deepcopy(MANIFEST)
        next(f for f in bad['functions'] if f.get('background'))['provisioned']=0
        with self.assertRaises(ValueError): d.validate(bad,RUNTIME,ENV)
        bad=copy.deepcopy(RUNTIME); bad['nasConfig']={}
        with self.assertRaises(ValueError): d.validate(MANIFEST,bad,ENV)
    def test_rejects_mutable_tags_and_credential_urls(self):
        with self.assertRaises(ValueError): d.validate(MANIFEST,RUNTIME,{**ENV,'IMAGE_TAG':'latest'})
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
    def test_permission_error_is_not_treated_as_missing_function(self):
        error=Exception('forbidden');error.status_code=403
        with self.assertRaises(Exception): d.Deployment(Mock(),m).optional(Mock(side_effect=error))
    def test_function_waits_for_completed_update(self):
        client=Mock(); client.get_function.side_effect=[NS(body=NS(state='Active',last_update_status='InProgress')),NS(body=NS(state='Active',last_update_status='Successful'))]
        sleep=Mock(); d.Deployment(client,m,sleep).wait_function('x');sleep.assert_called_once()

if __name__=='__main__': unittest.main()
