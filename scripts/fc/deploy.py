#!/usr/bin/env python3
"""FC 3.0 deployment using the pinned official SDK. No cloud calls without apply/rollback."""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
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
    for key in ('FC_ACCOUNT_ID', 'FC_REGION', 'FC_PREFIX', 'ALIYUN_OSS_BUCKET', 'RELEASE_SHA', 'FC_EXECUTION_ROLE'):
        need(env, key)
    if not re.fullmatch(r'[0-9]{6,32}', env['FC_ACCOUNT_ID']): raise ValueError('Invalid account ID')
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,30}', env['FC_REGION']): raise ValueError('Invalid region')
    if not re.fullmatch(r'acs:ram::' + re.escape(env['FC_ACCOUNT_ID']) + r':role/[A-Za-z0-9_.@-]+', env['FC_EXECUTION_ROLE']): raise ValueError('FC_EXECUTION_ROLE must belong to FC_ACCOUNT_ID')
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,40}', env['FC_PREFIX']): raise ValueError('Invalid function prefix')
    if not re.fullmatch(r'[a-f0-9]{40}', env['RELEASE_SHA']): raise ValueError('RELEASE_SHA must be full commit SHA')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', env['ALIYUN_OSS_BUCKET']): raise ValueError('Invalid OSS bucket')
    artifacts = runtime.get('artifacts', {})
    for f in manifest['functions']:
        artifact = artifacts.get(f['name'], {})
        if not re.fullmatch(r'[a-f0-9]{64}', artifact.get('sha256', '')): raise ValueError('Missing verified package digest: ' + f['name'])
        expected = f"{env.get('ALIYUN_OSS_PREFIX', 'gamevallies/prod/').strip('/')}/releases/{env['RELEASE_SHA']}/{artifact['sha256']}/{f['name']}.zip"
        if artifact.get('object') != expected: raise ValueError('Package path/commit mismatch: ' + f['name'])
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
        if f.get('background'):
            if f.get('provisioned') != 1:
                raise ValueError('Background services require one continuously active provisioned instance')
            if f['name'] == 'ai-engine' and f.get('disableOndemand') is not True:
                raise ValueError('ai-engine requires disableOndemand to keep a single worker instance')
            if f['name'] == 'game-service':
                if f.get('maxInstances', 2) < 2:
                    raise ValueError('game-service requires maxInstances >= 2 for admin/API overflow')
    if 'game-service' in names:
        if not re.fullmatch('[a-f0-9]{64}', common.get('FC_INTERNAL_TOKEN', '')): raise ValueError('Set 64 hex character FC_INTERNAL_TOKEN')
        for key in ('DATABASE_URL', 'REDIS_URL', 'JWT_SECRET', 'JWT_REFRESH_SECRET', 'ADMIN_TOKEN'):
            need(common, key)
        if 'gateway' in names: origin(need(env, 'FC_FRONTEND_URL'))
        game_env = {**common, **runtime.get('services', {}).get('game-service', {})}
        if game_env.get('OBJECT_STORAGE_PROVIDER') != 'aliyun-oss': raise ValueError('FC requires persistent OSS storage')
        for key in ('ALIYUN_OSS_ACCESS_KEY_ID', 'ALIYUN_OSS_ACCESS_KEY_SECRET', 'ALIYUN_OSS_BUCKET', 'ALIYUN_OSS_ENDPOINT', 'ALIYUN_OSS_PREFIX'):
            need(game_env, key)
        vpc = runtime.get('vpcConfig', {})
        if not all(vpc.get(k) for k in ('vpcId', 'vSwitchIds', 'securityGroupId')): raise ValueError('Set private database VPC configuration')


def function_body(f, runtime, env, endpoints):
    name = f['name']
    values = {**runtime.get('common', {}), **runtime.get('services', {}).get(name, {})}
    if name not in ('frontend', 'gateway', 'content'):
        values.update(FC_DEPLOYMENT='true', FC_SERVICE=name, PORT=str(f['port']), NODE_ENV='production', ENVIRONMENT='production')
        values.update(DATABASE_SCHEMA_MANAGED='true', ENABLE_LEGACY_RUNTIME_SCHEMA_BOOTSTRAP='false')
        values['GAMEVALLIES_CLOUD_REGION'] = env['FC_REGION']
        if name != 'ai-engine': values['NODE_OPTIONS'] = '--require=/code/fc-internal-auth.cjs'
        urls = {k: v for k, v in endpoints.items() if k not in ('gateway', 'content', 'frontend')}
        values['FC_INTERNAL_ORIGINS'] = ','.join(sorted(set(urls.values())))
        values['PUBLIC_APP_HOSTS'] = 'www.zlspace.ai'
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
                  'FRONTEND_UPSTREAM': env.get('FC_FRONTEND_URL', 'https://unconfigured.invalid')}
    artifact = runtime['artifacts'][name]
    code = {'ossBucketName': env['ALIYUN_OSS_BUCKET'], 'ossObjectName': artifact['object']}
    custom = {'command': ['/code/bootstrap'], 'port': f['port'],
              'healthCheckConfig': {'httpGetUrl': f['health'], 'initialDelaySeconds': 30, 'periodSeconds': 10, 'timeoutSeconds': 3, 'failureThreshold': 6, 'successThreshold': 1}}
    body = {'functionName': f"{env['FC_PREFIX']}-{name}", 'runtime': 'custom.debian12', 'code': code, 'customRuntimeConfig': custom,
            'cpu': f['cpu'], 'memorySize': f['memory'], 'diskSize': 10240 if name == 'ai-engine' else 512,
            'timeout': f.get('timeout', 900), 'instanceConcurrency': f['concurrency'],
            'internetAccess': True, 'role': env['FC_EXECUTION_ROLE'], 'environmentVariables': {('GAMEVALLIES_' + k if k.startswith('FC_') else k): v for k, v in values.items()},
            'disableOndemand': f.get('disableOndemand', False), 'disableInjectCredentials': 'Request',
            'description': 'GameVallies OSS ' + json.dumps(code, separators=(',', ':'))}
    for key in ('vpcConfig', 'logConfig'):
        if runtime.get(key): body[key] = runtime[key]
    # New installs do not require NAS. Existing mounts are left untouched.
    return body


def checkpoint_code(previous):
    description = previous.get('description', '')
    if not description.startswith('GameVallies OSS '):
        raise ValueError('Existing code function lacks an OSS rollback checkpoint; export it before migrating')
    code = json.loads(description[len('GameVallies OSS '):])
    if set(code) != {'ossBucketName', 'ossObjectName'} or not all(isinstance(v, str) and v for v in code.values()):
        raise ValueError('Invalid OSS rollback checkpoint')
    return code


def http_json(url, token, path, method='GET'):
    request = urllib.request.Request(origin(url) + path, method=method, headers={'x-gamevallies-internal-token': token})
    # A transport token must never follow a redirect to another host.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args): return None
    with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as res:
        data = res.read(65536)
        try: return json.loads(data) if data else {}
        except json.JSONDecodeError: return {}


def deployment_error(code, name):
    error = RuntimeError(code)
    error.code = code
    return error


def report_error(error, stage, name):
    # Log bounded identifiers only, never SDK messages or request/environment bodies.
    def safe(value):
        value = str(value or '')
        return value if re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value) else 'unavailable'
    data = getattr(error, 'data', None)
    data = data if isinstance(data, dict) else {}
    status = getattr(error, 'status_code', None)
    request_id = data.get('RequestId') or data.get('requestId')
    if isinstance(error, urllib.error.HTTPError):
        status = error.code
        request_id = error.headers.get('x-fc-request-id') if error.headers else None
    reason = getattr(error, 'reason', None)
    reason_type = type(reason).__name__ if isinstance(reason, BaseException) else None
    print(f"FC failure; stage={safe(stage)}; function={safe(name)}; type={safe(type(error).__name__)}; code={safe(getattr(error, 'code', None))}; status={safe(status)}; requestId={safe(request_id)}; reasonType={safe(reason_type)}", file=sys.stderr, flush=True)


class Deployment:
    def __init__(self, client, models, sleep=time.sleep):
        self.c, self.m, self.sleep = client, models, sleep

    def optional(self, action):
        try: return action()
        except Exception as e:
            if getattr(e, 'status_code', None) == 404 or getattr(e, 'code', '') in ('FunctionNotFound', 'TriggerNotFound'): return None
            raise

    def service_is_stopped(self, name):
        """Skip HTTP drain only when FC confirms no current or future capacity."""
        function = self.c.get_function(name, self.m.GetFunctionRequest()).body
        if function.disable_ondemand is not True:
            return False
        provision = self.c.get_provision_config(
            name, self.m.GetProvisionConfigRequest(qualifier='LATEST')).body
        # Missing/unknown fields and API failures are not proof of inactivity.
        return (provision.current == 0 and provision.target == 0
                and provision.default_target in (None, 0)
                and not provision.scheduled_actions
                and not provision.target_tracking_policies)

    def wait_function(self, name):
        for _ in range(90):
            f = self.c.get_function(name, self.m.GetFunctionRequest()).body
            if f.state == 'Failed' or f.last_update_status == 'Failed': raise deployment_error('FUNCTION_UPDATE_FAILED', name)
            if f.state == 'Active' and f.last_update_status in (None, 'Successful'): return f
            # ZIP runtimes may omit asynchronous image-state fields entirely.
            # Require persisted code; provisioning and HTTP health checks still follow.
            if (f.state is None and f.last_update_status is None
                    and getattr(f, 'runtime', None) == 'custom.debian12'
                    and getattr(f, 'code_checksum', None)
                    and (getattr(f, 'code_size', None) or 0) > 0):
                return f
            self.sleep(5)
        raise TimeoutError(f'Function did not become active: {name}')

    def provision(self, name, count):
        self.c.put_provision_config(name, self.m.PutProvisionConfigRequest(qualifier='LATEST', body=self.m.PutProvisionConfigInput(default_target=count, always_allocate_cpu=True, scheduled_actions=[], target_tracking_policies=[])))
        if not count: return
        for _ in range(90):
            p = self.c.get_provision_config(name, self.m.GetProvisionConfigRequest(qualifier='LATEST')).body
            # Provisioning is asynchronous; currentError can outlive the old revision.
            # Wait for a clean ready state instead of failing on the first snapshot.
            if not p.current_error and p.current == count and p.target == count and p.always_allocate_cpu: return
            self.sleep(5)
        raise deployment_error('PROVISIONING_FAILED' if p.current_error else 'PROVISIONING_TIMEOUT', name)

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

    def matching_version(self, name, current):
        # Compare every updateable configuration field, plus immutable code identity.
        # Descriptions are excluded: FC version descriptions can differ from LATEST.
        def fingerprint(function):
            data = function.to_map()
            config = self.m.UpdateFunctionInput().from_map(data).to_map()
            config.pop('description', None)
            config.pop('code', None)
            return (data.get('codeChecksum'), config)
        wanted = fingerprint(current)
        if not wanted[0]:
            return None
        token = None
        for _ in range(10):
            page = self.c.list_function_versions(name, self.m.ListFunctionVersionsRequest(
                direction='BACKWARD', limit=100, next_token=token)).body
            for version in page.versions or []:
                candidate = self.c.get_function(name, self.m.GetFunctionRequest(
                    qualifier=version.version_id)).body
                if fingerprint(candidate) == wanted:
                    return version.version_id
            token = page.next_token
            if not token:
                break
        return None

    def version(self, name):
        current = self.c.get_function(name, self.m.GetFunctionRequest()).body
        existing = self.matching_version(name, current)
        if existing:
            print(f'Reusing verified matching FC version: {name}', flush=True)
            return existing
        try:
            return self.c.publish_function_version(name, self.m.PublishFunctionVersionRequest(
                body=self.m.PublishVersionInput(description='GameVallies deployment checkpoint'))).body.version_id
        except Exception as error:
            if getattr(error, 'code', None) != 'VersionPublishError':
                raise
            # A concurrent publisher may have created the same revision.
            latest = self.c.get_function(name, self.m.GetFunctionRequest()).body
            if latest.to_map() == current.to_map():
                existing = self.matching_version(name, current)
                if existing:
                    return existing
            raise

    def restore(self, name, version, code=None):
        previous = self.c.get_function(name, self.m.GetFunctionRequest(qualifier=version)).body.to_map()
        if code:
            previous['description'] = 'GameVallies OSS ' + json.dumps(code)
        self.restore_configuration(name, previous)

    def restore_configuration(self, name, previous):
        previous = dict(previous)
        # GetFunction returns handler='' for custom runtimes, but UpdateFunction
        # rejects an explicitly empty handler (InvalidArgument). It is unused by
        # these runtimes; retain real handlers when restoring managed runtimes.
        if previous.get('runtime', '').startswith('custom') and not previous.get('handler'):
            previous.pop('handler', None)
        if previous.get('runtime') == 'custom-container':
            container = previous.get('customContainerConfig', {})
            previous['customContainerConfig'] = {k: v for k, v in container.items() if k in ('image', 'port', 'command', 'entrypoint', 'healthCheckConfig', 'acrInstanceId', 'registryConfig', 'accelerationType')}
        else:
            # GetFunction omits code. Recover the immutable OSS reference from the
            # selected checkpoint, never from the current release or LATEST.
            previous['code'] = checkpoint_code(previous)
            previous.pop('customContainerConfig', None)
        body = self.m.UpdateFunctionInput().from_map(previous)
        self.c.update_function(name, self.m.UpdateFunctionRequest(body=body))
        self.wait_function(name)

    def migrate_database(self, manifest, runtime, env, endpoints=None):
        game = next((f for f in manifest['functions'] if f['name'] == 'game-service'), None)
        if not game:
            return
        body = function_body(game, runtime, env, {})
        name = env['FC_PREFIX'] + '-db-migrate'
        body.update(functionName=name, cpu=0.5, memorySize=1024, timeout=900,
                    instanceConcurrency=1, disableOndemand=False, internetAccess=False)
        body['environmentVariables'] = {
            'DATABASE_URL': runtime['common']['DATABASE_URL'],
            'NODE_ENV': 'production', 'LD_LIBRARY_PATH': '/code/lib',
            'PATH': '/code/bin:/usr/local/bin:/usr/bin:/bin',
            'CHECKPOINT_DISABLE': '1',
            'GAMEVALLIES_CLOUD_REGION': env['FC_REGION'],
            'GAMEVALLIES_FC_PREFIX': env['FC_PREFIX'],
            'AI_ENGINE_URL': (endpoints or {}).get('ai-engine', ''),
            'PRISMA_SCHEMA_ENGINE_BINARY': '/code/node_modules/@prisma/engines/schema-engine-debian-openssl-3.0.x',
            'PRISMA_QUERY_ENGINE_LIBRARY': '/code/node_modules/.prisma/client/libquery_engine-debian-openssl-3.0.x.so.node',
        }
        body['customRuntimeConfig'] = {
            'command': ['/code/bin/node', '/code/deploy/fc/migration-server.cjs'], 'port': 9000,
            'healthCheckConfig': {'httpGetUrl': '/health', 'initialDelaySeconds': 5,
                                 'periodSeconds': 10, 'timeoutSeconds': 3, 'failureThreshold': 6, 'successThreshold': 1},
        }
        old = self.optional(lambda: self.c.get_function(name, self.m.GetFunctionRequest()))
        if old:
            # Never turn a pre-existing unrelated function into a DB executor.
            checkpoint_code(old.body.to_map())
            self.c.update_function(name, self.m.UpdateFunctionRequest(body=self.m.UpdateFunctionInput().from_map(body)))
        else:
            self.c.create_function(self.m.CreateFunctionRequest(body=self.m.CreateFunctionInput().from_map(body)))
        self.wait_function(name)
        self.c.put_concurrency_config(name, self.m.PutConcurrencyConfigRequest(body=self.m.PutConcurrencyInput(reserved_concurrency=1)))
        try:
            from alibabacloud_tea_util.models import RuntimeOptions
            response = self.c.invoke_function_with_options(name,
                self.m.InvokeFunctionRequest(body=io.BytesIO(b'{}'), qualifier='LATEST'),
                self.m.InvokeFunctionHeaders(x_fc_invocation_type='Sync', x_fc_log_type='None'),
                RuntimeOptions(read_timeout=960000, connect_timeout=10000, autoretry=False))
            raw = response.body.read(65536)
            result = json.loads(raw)
            if response.status_code != 200 or result.get('ok') is not True or result.get('stage') != 'database-ready':
                code = result.get('code', '')
                code = code if re.fullmatch(r'[A-Z][A-Z0-9_]{1,80}', str(code)) else 'UNKNOWN'
                error = RuntimeError('Database migration failed: ' + code)
                error.code = code
                raise error
            print('Database migrations and production seed verified.', flush=True)
        finally:
            # No provisioned instances or public trigger; block invocation between releases.
            self.c.update_function(name, self.m.UpdateFunctionRequest(body=self.m.UpdateFunctionInput(disable_ondemand=True)))

    def apply(self, manifest, runtime, env, output):
        entries, endpoints, touched = [], {}, []
        stopped_snapshots = {}
        prefix = env['FC_PREFIX']
        token = runtime.get('common', {}).get('FC_INTERNAL_TOKEN', '')
        old_game_url = None
        if any(f['name'] == 'game-service' for f in manifest['functions']):
            previous = self.optional(lambda: self.c.get_trigger(prefix + '-game-service', TRIGGER))
            if previous: old_game_url = origin(previous.body.http_trigger.url_internet)
        for f in manifest['functions']:
            old = self.optional(lambda: self.c.get_function(prefix + '-' + f['name'], self.m.GetFunctionRequest()))
            if old and old.body.runtime != 'custom-container': checkpoint_code(old.body.to_map())
        drained = False
        stage, name = 'drain', prefix + '-game-service'
        try:
            if old_game_url and self.service_is_stopped(prefix + '-game-service'):
                print('FC drain skipped: game-service has on-demand disabled and zero provisioned capacity.', flush=True)
                old_game_url = None
            if old_game_url:
                http_json(old_game_url, token, '/__fc/drain', 'POST'); drained = True
                stable = 0
                for _ in range(240):
                    status = http_json(old_game_url, token, '/__fc/status')
                    stable = stable + 1 if status.get('pending') == 0 and status.get('maintenance') else 0
                    if stable >= 3: break
                    self.sleep(10)
                else: raise TimeoutError('Tasks did not drain; no functions updated')
            stage = 'discover-endpoints'
            # Discover real FC trigger URLs without overwriting existing service configuration.
            for f in manifest['functions']:
                name = prefix + '-' + f['name']
                old = self.optional(lambda: self.c.get_function(name, self.m.GetFunctionRequest()))
                stopped = bool(old) and self.service_is_stopped(name)
                previous_code = checkpoint_code(old.body.to_map()) if old and old.body.runtime != 'custom-container' else None
                if stopped:
                    # Keep credentials only in process memory, never in release artifacts.
                    stopped_snapshots[name] = old.body.to_map()
                stage = 'checkpoint-version'
                version = self.version(name) if old and not stopped else None
                entry = {'name': name, 'service': f['name'], 'previousVersion': version, 'provisioned': f.get('provisioned', 0)}
                if previous_code: entry['previousCode'] = previous_code
                entries.append(entry)
                if not old:
                    body = function_body(f, runtime, env, {})
                    # No worker starts until all endpoints/configuration have been resolved.
                    body['disableOndemand'] = True
                    self.c.create_function(self.m.CreateFunctionRequest(body=self.m.CreateFunctionInput().from_map(body)))
                    self.wait_function(name)
                endpoints[f['name']] = self.trigger(name)
                entry['url'] = endpoints[f['name']]
            # New functions are disabled above. Initialize the DB and route catalog
            # before starting/updating any application workers.
            stage, name = 'database-migration', prefix + '-db-migrate'
            self.migrate_database(manifest, runtime, env, endpoints)
            for f, entry in zip(manifest['functions'], entries):
                name = entry['name']
                stage = 'update-function'
                print(f'FC stage={stage}; function={name}', flush=True)
                touched.append(entry)
                body = function_body(f, runtime, env, endpoints)
                self.c.update_function(name, self.m.UpdateFunctionRequest(body=self.m.UpdateFunctionInput().from_map(body)))
                self.wait_function(name)
                self.c.put_concurrency_config(name, self.m.PutConcurrencyConfigRequest(body=self.m.PutConcurrencyInput(reserved_concurrency=f['concurrency'] * f.get('maxInstances', 2))))
                stage = 'provision-instances'
                print(f'FC stage={stage}; function={name}', flush=True)
                self.provision(name, f.get('provisioned', 0))
                actual = self.wait_function(name)
                if f.get('background') and f['name'] == 'ai-engine' and not actual.disable_ondemand: raise deployment_error('BACKGROUND_ISOLATION_FAILED', name)
                # HTTP cold start and custom health check are both exercised.
                stage = 'http-health-check'
                print(f'FC stage={stage}; function={name}', flush=True)
                for attempt in range(18):
                    try:
                        http_json(entry['url'], token, f['health']); break
                    except Exception as health_error:
                        report_error(health_error, stage, name)
                        if attempt == 17: raise deployment_error('HTTP_HEALTH_CHECK_FAILED', name) from None
                        self.sleep(5)
                stage = 'publish-version'
                entry['version'] = self.version(name)
                print(f'Verified FC function: {name}', flush=True)
            Path(output).write_text(json.dumps({'commit': env['RELEASE_SHA'], 'region': env['FC_REGION'], 'accountId': env['FC_ACCOUNT_ID'], 'functions': entries}, indent=2) + '\n')
            print('FC HTTP health checks passed; complete business acceptance before switching DNS.')
        except Exception as original:
            report_error(original, stage, name)
            failures = []
            for entry in reversed(touched):
                try:
                    if entry['name'] in stopped_snapshots:
                        self.restore_configuration(entry['name'], stopped_snapshots[entry['name']])
                        self.c.put_provision_config(entry['name'], self.m.PutProvisionConfigRequest(
                            qualifier='LATEST', body=self.m.PutProvisionConfigInput(default_target=0)))
                    elif entry['previousVersion']:
                        self.restore(entry['name'], entry['previousVersion'], entry.get('previousCode'))
                        self.provision(entry['name'], entry['provisioned'])
                    else:
                        # New installations have no rollback target. Keep resources for diagnosis.
                        self.c.update_function(entry['name'], self.m.UpdateFunctionRequest(body=self.m.UpdateFunctionInput(disable_ondemand=True)))
                        self.c.put_provision_config(entry['name'], self.m.PutProvisionConfigRequest(qualifier='LATEST', body=self.m.PutProvisionConfigInput(default_target=0)))
                except Exception as rollback_error:
                    report_error(rollback_error, 'rollback', entry['name'])
                    failures.append(entry['name'])
            if failures: print('FC rollback incomplete', file=sys.stderr)
            raise
        finally:
            if drained:
                active_error = sys.exc_info()[0] is not None
                try:
                    http_json(old_game_url, token, '/__fc/resume', 'POST')
                except Exception as resume_error:
                    report_error(resume_error, 'resume', prefix + '-game-service')
                    if not active_error: raise


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
    config.endpoint = f"fcv3.{os.environ['FC_REGION']}.aliyuncs.com"
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
                deployment.restore(entry['name'], entry['previousVersion'], entry.get('previousCode'))
                deployment.provision(entry['name'], entry['provisioned'])
            print('Previous FC versions restored; verify business health.')
        finally:
            if game: http_json(game['url'], token, '/__fc/resume', 'POST')


if __name__ == '__main__':
    try: main()
    except Exception as e:
        # SDK exceptions may contain full request bodies/environment values.
        # Only allow bounded identifier fields; never print SDK message/data bodies.
        import re
        def identifier(value):
            value = str(value or '')
            return value if re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value) else 'unavailable'
        data = getattr(e, 'data', None)
        data = data if isinstance(data, dict) else {}
        code = identifier(getattr(e, 'code', None))
        status = identifier(getattr(e, 'status_code', None) or data.get('statusCode'))
        request_id = identifier(data.get('RequestId') or data.get('requestId') or getattr(e, 'request_id', None))
        print(f'FC deployment stopped ({type(e).__name__}); code={code}; status={status}; requestId={request_id}', file=sys.stderr)
        sys.exit(1)
