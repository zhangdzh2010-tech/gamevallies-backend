#!/usr/bin/env python3
"""FC 3.0 deployment using the pinned official SDK. No cloud calls without apply/rollback."""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']
TRIGGER = 'gamevallies-http'


def need(env, key):
    value = env.get(key, '').strip()
    if not value or 'REPLACE_' in value:
        raise ValueError(f'Missing {key}')
    return value


def origin(value):
    u = urlsplit(value)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.path not in ('', '/') or u.query or u.fragment:
        raise ValueError('Expected HTTPS origin without credentials or path')
    return value.rstrip('/')


def validate(manifest, runtime, env):
    for key in ('FC_ACCOUNT_ID', 'FC_REGION', 'FC_PREFIX', 'ACR_REGISTRY', 'ACR_NAMESPACE', 'IMAGE_TAG', 'FC_EXECUTION_ROLE'):
        need(env, key)
    if not re.fullmatch(r'[0-9]{6,32}', env['FC_ACCOUNT_ID']): raise ValueError('Invalid account ID')
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,30}', env['FC_REGION']): raise ValueError('Invalid region')
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,40}', env['FC_PREFIX']): raise ValueError('Invalid function prefix')
    if not re.fullmatch(r'[a-f0-9]{40}', env['IMAGE_TAG']): raise ValueError('IMAGE_TAG must be full commit SHA')
    if not re.fullmatch(r'[a-zA-Z0-9.-]+', env['ACR_REGISTRY']): raise ValueError('Invalid registry')
    if not re.fullmatch(r'[a-z0-9_-]+', env['ACR_NAMESPACE']): raise ValueError('Invalid namespace')
    common = runtime.get('common', {})
    for values in [common, *runtime.get('services', {}).values()]:
        if not isinstance(values, dict) or any(not isinstance(v, str) for v in values.values()):
            raise ValueError('Runtime environment values must be strings')
    if 'REPLACE_' in json.dumps(runtime) or 'RDS_PRIVATE_HOST' in json.dumps(runtime) or 'REDIS_PRIVATE_HOST' in json.dumps(runtime): raise ValueError('Runtime configuration still contains placeholders')
    names = [x['name'] for x in manifest['functions']]
    if len(names) != len(set(names)): raise ValueError('Duplicate functions')
    for f in manifest['functions']:
        if not 1 <= f['concurrency'] <= 200 or not 1 <= f.get('maxInstances', 2) <= 20: raise ValueError('Invalid concurrency limits')
        if not re.fullmatch(r'[a-z][a-z0-9-]+', f['name']): raise ValueError('Invalid function name')
        if f.get('background') and (f.get('provisioned') != 1 or not f.get('disableOndemand')):
            raise ValueError('Background services require one continuously active provisioned instance and no on-demand replicas')
    if 'game-service' in names:
        if not re.fullmatch('[a-f0-9]{64}', common.get('FC_INTERNAL_TOKEN', '')): raise ValueError('Set 64 hex character FC_INTERNAL_TOKEN')
        for key in ('DATABASE_URL', 'REDIS_URL', 'JWT_SECRET', 'JWT_REFRESH_SECRET', 'ADMIN_TOKEN'):
            need(common, key)
        origin(need(env, 'FC_FRONTEND_URL'))
        game_env = {**common, **runtime.get('services', {}).get('game-service', {})}
        mounts = runtime.get('nasConfig', {}).get('mountPoints', [])
        upload = game_env.get('APP_RELEASE_UPLOAD_DIR', '')
        if not upload or not any(upload.startswith(m.get('mountDir', '/impossible').rstrip('/') + '/') for m in mounts):
            raise ValueError('APP_RELEASE_UPLOAD_DIR must be under an explicitly configured NAS mount')
        vpc = runtime.get('vpcConfig', {})
        if not all(vpc.get(k) for k in ('vpcId', 'vSwitchIds', 'securityGroupId')): raise ValueError('Set private database/NAS VPC configuration')


def function_body(f, runtime, env, endpoints):
    name = f['name']
    values = {**runtime.get('common', {}), **runtime.get('services', {}).get(name, {})}
    if name not in ('frontend', 'gateway', 'content'):
        values.update(FC_DEPLOYMENT='true', FC_SERVICE=name, PORT=str(f['port']), NODE_ENV='production', ENVIRONMENT='production')
        if name != 'ai-engine': values['NODE_OPTIONS'] = '--require=/app/fc-internal-auth.cjs'
        urls = {k: v for k, v in endpoints.items() if k not in ('gateway', 'content', 'frontend')}
        values['FC_INTERNAL_ORIGINS'] = ','.join(sorted(set(urls.values())))
        values.update(AI_ENGINE_URL=urls.get('ai-engine', 'https://unconfigured.invalid'),
                      AI_ENGINE_URL_CN_SHANGHAI=urls.get('ai-engine', 'https://unconfigured.invalid'),
                      GAME_SERVICE_UPSTREAM_URL=urls.get('game-service', 'https://unconfigured.invalid'),
                      FEED_SERVICE_UPSTREAM_URL=urls.get('feed-service', 'https://unconfigured.invalid'),
                      SERVICE_REGION='cn_shanghai', AI_ENGINE_DEFAULT_REGION='cn_shanghai')
    if name in ('gateway', 'content'):
        values = {'FC_INTERNAL_TOKEN': runtime['common']['FC_INTERNAL_TOKEN'], 'GATEWAY_MODE': 'content' if name == 'content' else 'app',
                  'GAME_UPSTREAM': endpoints.get('game-service', 'https://unconfigured.invalid'),
                  'USER_UPSTREAM': endpoints.get('user-service', 'https://unconfigured.invalid'),
                  'AI_UPSTREAM': endpoints.get('ai-engine', 'https://unconfigured.invalid'),
                  'FRONTEND_UPSTREAM': env['FC_FRONTEND_URL']}
    image = f"{env['ACR_REGISTRY']}/{env['ACR_NAMESPACE']}/{f.get('image', name)}:{env['IMAGE_TAG']}"
    container = {'image': image, 'port': f['port'], 'healthCheckConfig': {'httpGetUrl': f['health'], 'initialDelaySeconds': 30, 'periodSeconds': 10, 'timeoutSeconds': 3, 'failureThreshold': 6, 'successThreshold': 1}}
    if env.get('ACR_INSTANCE_ID'): container['acrInstanceId'] = env['ACR_INSTANCE_ID']
    body = {'functionName': f"{env['FC_PREFIX']}-{name}", 'runtime': 'custom-container', 'customContainerConfig': container,
            'cpu': f['cpu'], 'memorySize': f['memory'], 'diskSize': 10240 if name == 'ai-engine' else 512,
            'timeout': f.get('timeout', 900), 'instanceConcurrency': f['concurrency'],
            'internetAccess': True, 'role': env['FC_EXECUTION_ROLE'], 'environmentVariables': values,
            'disableOndemand': f.get('disableOndemand', False), 'disableInjectCredentials': 'Request',
            'description': f"GameVallies FC {env['IMAGE_TAG']}"}
    for key in ('vpcConfig', 'logConfig'):
        if runtime.get(key): body[key] = runtime[key]
    if name == 'game-service': body['nasConfig'] = runtime['nasConfig']
    return body


def http_json(url, token, path, method='GET'):
    request = urllib.request.Request(origin(url) + path, method=method, headers={'x-fc-internal-token': token})
    # A transport token must never follow a redirect to another host.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args): return None
    with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as res:
        data = res.read(65536)
        try: return json.loads(data) if data else {}
        except json.JSONDecodeError: return {}


class Deployment:
    def __init__(self, client, models, sleep=time.sleep):
        self.c, self.m, self.sleep = client, models, sleep

    def optional(self, action):
        try: return action()
        except Exception as e:
            if getattr(e, 'status_code', None) == 404 or getattr(e, 'code', '') in ('FunctionNotFound', 'TriggerNotFound'): return None
            raise

    def wait_function(self, name):
        for _ in range(90):
            f = self.c.get_function(name, self.m.GetFunctionRequest()).body
            if f.state == 'Failed' or f.last_update_status == 'Failed': raise RuntimeError(f'Function update failed: {name}')
            if f.state == 'Active' and f.last_update_status in (None, 'Successful'): return f
            self.sleep(5)
        raise TimeoutError(f'Function did not become active: {name}')

    def provision(self, name, count):
        self.c.put_provision_config(name, self.m.PutProvisionConfigRequest(qualifier='LATEST', body=self.m.PutProvisionConfigInput(default_target=count, always_allocate_cpu=True, scheduled_actions=[], target_tracking_policies=[])))
        if not count: return
        for _ in range(90):
            p = self.c.get_provision_config(name, self.m.GetProvisionConfigRequest(qualifier='LATEST')).body
            if p.current_error: raise RuntimeError(f'Provisioning failed: {name}')
            if p.current == count and p.target == count and p.always_allocate_cpu: return
            self.sleep(5)
        raise TimeoutError(f'Provisioning not ready: {name}')

    def trigger(self, name):
        t = self.optional(lambda: self.c.get_trigger(name, TRIGGER))
        config = {'authType': 'anonymous', 'methods': METHODS, 'disableURLInternet': False}
        if t:
            old = json.loads(t.body.trigger_config)
            if t.body.qualifier != 'LATEST' or old.get('authType') != 'anonymous':
                raise ValueError(f'Review existing trigger policy before replacing: {name}')
        else:
            self.c.create_trigger(name, self.m.CreateTriggerRequest(body=self.m.CreateTriggerInput(trigger_name=TRIGGER, trigger_type='http', qualifier='LATEST', trigger_config=json.dumps(config))))
        t = self.c.get_trigger(name, TRIGGER).body
        return origin(t.http_trigger.url_internet)

    def version(self, name):
        return self.c.publish_function_version(name, self.m.PublishFunctionVersionRequest(body=self.m.PublishVersionInput(description='GameVallies deployment checkpoint'))).body.version_id

    def restore(self, name, version):
        previous = self.c.get_function(name, self.m.GetFunctionRequest(qualifier=version)).body.to_map()
        # from_map accepts writable fields only; response metadata is not sent.
        body = self.m.UpdateFunctionInput().from_map(previous)
        self.c.update_function(name, self.m.UpdateFunctionRequest(body=body))
        self.wait_function(name)

    def apply(self, manifest, runtime, env, output):
        entries, endpoints, touched = [], {}, []
        prefix = env['FC_PREFIX']
        token = runtime.get('common', {}).get('FC_INTERNAL_TOKEN', '')
        old_game_url = None
        if any(f['name'] == 'game-service' for f in manifest['functions']):
            previous = self.optional(lambda: self.c.get_trigger(prefix + '-game-service', TRIGGER))
            if previous: old_game_url = origin(previous.body.http_trigger.url_internet)
        drained = False
        try:
            if old_game_url:
                http_json(old_game_url, token, '/__fc/drain', 'POST'); drained = True
                stable = 0
                for _ in range(240):
                    status = http_json(old_game_url, token, '/__fc/status')
                    stable = stable + 1 if status.get('pending') == 0 and status.get('maintenance') else 0
                    if stable >= 3: break
                    self.sleep(10)
                else: raise TimeoutError('Tasks did not drain; no functions updated')
            # Discover real FC trigger URLs without overwriting existing service configuration.
            for f in manifest['functions']:
                name = prefix + '-' + f['name']
                old = self.optional(lambda: self.c.get_function(name, self.m.GetFunctionRequest()))
                version = self.version(name) if old else None
                entry = {'name': name, 'service': f['name'], 'previousVersion': version, 'provisioned': f.get('provisioned', 0)}
                entries.append(entry)
                if not old:
                    body = function_body(f, runtime, env, {})
                    # No worker starts until all endpoints/configuration have been resolved.
                    body['disableOndemand'] = True
                    self.c.create_function(self.m.CreateFunctionRequest(body=self.m.CreateFunctionInput().from_map(body)))
                    self.wait_function(name)
                endpoints[f['name']] = self.trigger(name)
                entry['url'] = endpoints[f['name']]
            for f, entry in zip(manifest['functions'], entries):
                name = entry['name']
                touched.append(entry)
                body = function_body(f, runtime, env, endpoints)
                self.c.update_function(name, self.m.UpdateFunctionRequest(body=self.m.UpdateFunctionInput().from_map(body)))
                self.wait_function(name)
                self.c.put_concurrency_config(name, self.m.PutConcurrencyConfigRequest(body=self.m.PutConcurrencyInput(reserved_concurrency=f['concurrency'] * f.get('maxInstances', 2))))
                self.provision(name, f.get('provisioned', 0))
                actual = self.wait_function(name)
                if f.get('background') and not actual.disable_ondemand: raise RuntimeError('Background on-demand isolation not applied')
                # HTTP cold start and custom health check are both exercised.
                for attempt in range(18):
                    try:
                        http_json(entry['url'], token, f['health']); break
                    except Exception:
                        if attempt == 17: raise RuntimeError(f'HTTP health check failed: {name}') from None
                        self.sleep(5)
                entry['version'] = self.version(name)
                print(f'Verified FC function: {name}', flush=True)
            Path(output).write_text(json.dumps({'commit': env['IMAGE_TAG'], 'region': env['FC_REGION'], 'accountId': env['FC_ACCOUNT_ID'], 'functions': entries}, indent=2) + '\n')
            print('FC HTTP health checks passed; complete business acceptance before switching DNS.')
        except Exception:
            failures = []
            for entry in reversed(touched):
                try:
                    if entry['previousVersion']:
                        self.restore(entry['name'], entry['previousVersion'])
                        self.provision(entry['name'], entry['provisioned'])
                    else:
                        # New installations have no rollback target. Keep resources for diagnosis.
                        self.c.update_function(entry['name'], self.m.UpdateFunctionRequest(body=self.m.UpdateFunctionInput(disable_ondemand=True)))
                        self.c.put_provision_config(entry['name'], self.m.PutProvisionConfigRequest(qualifier='LATEST', body=self.m.PutProvisionConfigInput(default_target=0)))
                except Exception: failures.append(entry['name'])
            if failures: raise RuntimeError('Rollback incomplete: ' + ', '.join(failures)) from None
            raise
        finally:
            if drained:
                http_json(old_game_url, token, '/__fc/resume', 'POST')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['validate', 'apply', 'rollback'])
    p.add_argument('--manifest', default='deploy/fc/functions.json')
    p.add_argument('--runtime', required=True)
    p.add_argument('--release', default='fc-release.json')
    args = p.parse_args()
    manifest = json.loads(Path(args.manifest).read_text())
    runtime = json.loads(Path(args.runtime).read_text())
    validate(manifest, runtime, os.environ)
    if args.command == 'validate':
        # Validate against official SDK field types without making any API call.
        from alibabacloud_fc20230330 import models as m
        for f in manifest['functions']: m.CreateFunctionInput().from_map(function_body(f, runtime, os.environ, {})).validate()
        print('FC configuration validated; no cloud mutations performed.'); return
    from alibabacloud_fc20230330.client import Client
    from alibabacloud_fc20230330 import models as m
    from alibabacloud_tea_openapi.models import Config
    config = Config(access_key_id=need(os.environ, 'ALIBABA_CLOUD_ACCESS_KEY_ID'), access_key_secret=need(os.environ, 'ALIBABA_CLOUD_ACCESS_KEY_SECRET'), security_token=os.getenv('ALIBABA_CLOUD_SECURITY_TOKEN'))
    config.endpoint = f"{os.environ['FC_ACCOUNT_ID']}.{os.environ['FC_REGION']}.fc.aliyuncs.com"
    config.connect_timeout, config.read_timeout = 10000, 60000
    deployment = Deployment(Client(config), m)
    if args.command == 'apply': deployment.apply(manifest, runtime, os.environ, args.release)
    else:
        release = json.loads(Path(args.release).read_text())
        if release['region'] != os.environ['FC_REGION'] or release['accountId'] != os.environ['FC_ACCOUNT_ID']: raise ValueError('Rollback account/region mismatch')
        game = next((x for x in release['functions'] if x['service'] == 'game-service'), None)
        token = runtime.get('common', {}).get('FC_INTERNAL_TOKEN', '')
        try:
            if game:
                http_json(game['url'], token, '/__fc/drain', 'POST')
                if http_json(game['url'], token, '/__fc/status')['pending']: raise RuntimeError('Wait for tasks to drain before rollback')
            for entry in reversed(release['functions']):
                if not entry.get('previousVersion'): raise ValueError('First installation has no previous version')
                if not entry['name'].startswith(os.environ['FC_PREFIX'] + '-'): raise ValueError('Rollback function prefix mismatch')
                deployment.restore(entry['name'], entry['previousVersion'])
                deployment.provision(entry['name'], entry['provisioned'])
            print('Previous FC versions restored; verify business health.')
        finally:
            if game: http_json(game['url'], token, '/__fc/resume', 'POST')


if __name__ == '__main__':
    try: main()
    except Exception as e:
        # SDK exceptions may contain full request bodies/environment values.
        print(f'FC deployment stopped ({type(e).__name__}); inspect protected cloud logs/configuration.', file=sys.stderr)
        sys.exit(1)
