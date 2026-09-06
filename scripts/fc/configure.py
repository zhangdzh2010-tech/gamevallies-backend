#!/usr/bin/env python3
"""Translate individual GitHub Aliyun settings to a private temporary FC config."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile
from deploy import need, origin


def package_index(directory, manifest, env):
    result = {}
    prefix = env.get('ALIYUN_OSS_PREFIX', 'gamevallies/prod/').strip('/')
    if not prefix or '..' in prefix.split('/') or not re.fullmatch(r'[a-zA-Z0-9/_-]+', prefix):
        raise ValueError('Invalid ALIYUN_OSS_PREFIX')
    for f in manifest['functions']:
        path = Path(directory) / (f['name'] + '.zip')
        if not 0 < path.stat().st_size <= 500 * 1024 * 1024: raise ValueError('FC ZIP size limit exceeded')
        with zipfile.ZipFile(path) as archive:
            bootstrap = archive.getinfo('bootstrap')
            if not (bootstrap.external_attr >> 16) & 0o111: raise ValueError('bootstrap must be executable')
            for entry in archive.infolist():
                parts = Path(entry.filename).parts
                if entry.filename.startswith('/') or '..' in parts or any(p.startswith('.env') or p == '.git' for p in parts):
                    raise ValueError('Unsafe ZIP entry')
            if archive.testzip(): raise ValueError('Corrupt code package')
        with path.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        result[f['name']] = {'sha256': digest, 'object': f"{prefix}/releases/{need(env, 'RELEASE_SHA')}/{digest}/{f['name']}.zip"}
    return result


def runtime_config(env, backend):
    runtime = {'common': {}, 'services': {}}
    public = origin(env.get('PUBLIC_ORIGIN') or 'https://zlspace.clawworks.cn')
    content = origin(need(env, 'CONTENT_ORIGIN'))
    if content == public: raise ValueError('CONTENT_ORIGIN must be separate from the application')
    if not backend: return runtime
    common = runtime['common']
    for key in ('DATABASE_URL', 'REDIS_URL', 'JWT_SECRET', 'JWT_REFRESH_SECRET', 'ADMIN_TOKEN', 'FC_INTERNAL_TOKEN'):
        common[key] = need(env, key)
    common.update(CORS_ORIGIN=public, CORS_ORIGINS=json.dumps([public]), FRONTEND_URL=public,
                  PUBLIC_API_BASE_URL=public, APP_URL=public, BUNDLE_CDN_ENABLED='false')
    runtime['services']['ai-engine'] = {k: need(env, k) for k in ('LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL')}
    runtime['services']['ai-engine']['LLM_MODE'] = 'real'
    game = {k: need(env, k) for k in ('ALIYUN_OSS_ACCESS_KEY_ID', 'ALIYUN_OSS_ACCESS_KEY_SECRET', 'ALIYUN_OSS_BUCKET', 'ALIYUN_OSS_REGION', 'ALIYUN_OSS_ENDPOINT', 'ALIYUN_OSS_PREFIX')}
    if env.get('OBJECT_STORAGE_PROVIDER', 'aliyun-oss') != 'aliyun-oss': raise ValueError('Expected aliyun-oss')
    if game['ALIYUN_OSS_REGION'] != env['FC_REGION']: raise ValueError('OSS/FC regions must match')
    if game['ALIYUN_OSS_ENDPOINT'] != f"https://oss-{env['FC_REGION']}.aliyuncs.com": raise ValueError('Expected regional HTTPS OSS endpoint')
    game.update(OBJECT_STORAGE_PROVIDER='aliyun-oss', GENERATION_QUEUE_ENABLED='true', GENERATION_QUEUE_WORKER_CONCURRENCY='2')
    runtime['services']['game-service'] = game
    runtime['vpcConfig'] = {'vpcId': need(env, 'FC_VPC_ID'), 'vSwitchIds': [v.strip() for v in need(env, 'FC_VSWITCH_IDS').split(',') if v.strip()], 'securityGroupId': need(env, 'FC_SECURITY_GROUP_ID')}
    if env.get('FC_LOG_PROJECT') or env.get('FC_LOG_STORE'):
        runtime['logConfig'] = {'project': need(env, 'FC_LOG_PROJECT'), 'logstore': need(env, 'FC_LOG_STORE')}
    return runtime


def upload(directory, artifacts, env):
    import oss2
    # Deployment credentials write release packages; application OSS credentials are
    # injected only into game-service and are never included in the ZIP.
    token = env.get('ALIBABA_CLOUD_SECURITY_TOKEN')
    args = [need(env, 'ALIBABA_CLOUD_ACCESS_KEY_ID'), need(env, 'ALIBABA_CLOUD_ACCESS_KEY_SECRET')]
    auth = oss2.StsAuth(*args, token) if token else oss2.Auth(*args)
    bucket = oss2.Bucket(auth, f"https://oss-{need(env, 'FC_REGION')}.aliyuncs.com", need(env, 'ALIYUN_OSS_BUCKET'))
    for name, entry in artifacts.items():
        path = Path(directory) / (name + '.zip')
        with path.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != entry['sha256']: raise ValueError('Artifact changed after validation')
        try:
            bucket.put_object_from_file(entry['object'], str(path), headers={'x-oss-forbid-overwrite': 'true', 'x-oss-meta-sha256': digest})
        except oss2.exceptions.ServerError as error:
            if error.status != 409: raise
            meta = bucket.head_object(entry['object'])
            if meta.headers.get('x-oss-meta-sha256') != digest or meta.content_length != path.stat().st_size:
                raise ValueError('Existing immutable artifact differs') from None
        print('OSS package ready: ' + name)


def check_settings(env, manifest):
    backend = any(f['name'] == 'game-service' for f in manifest['functions'])
    required = ['FC_ACCOUNT_ID', 'FC_REGION', 'FC_PREFIX', 'FC_EXECUTION_ROLE', 'ALIYUN_OSS_BUCKET', 'CONTENT_ORIGIN', 'RELEASE_SHA']
    if backend:
        required += ['FC_FRONTEND_URL', 'DATABASE_URL', 'REDIS_URL', 'JWT_SECRET', 'JWT_REFRESH_SECRET', 'ADMIN_TOKEN', 'FC_INTERNAL_TOKEN', 'FC_VPC_ID', 'FC_VSWITCH_IDS', 'FC_SECURITY_GROUP_ID', 'LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL', 'ALIYUN_OSS_ACCESS_KEY_ID', 'ALIYUN_OSS_ACCESS_KEY_SECRET', 'ALIYUN_OSS_REGION', 'ALIYUN_OSS_ENDPOINT', 'ALIYUN_OSS_PREFIX']
    missing = [key for key in required if not env.get(key, '').strip()]
    if missing: raise ValueError('Missing configuration: ' + ', '.join(missing))
    runtime = runtime_config(env, backend)
    prefix = env.get('ALIYUN_OSS_PREFIX', 'gamevallies/prod/').strip('/')
    runtime['artifacts'] = {f['name']: {'sha256': '0'*64, 'object': f"{prefix}/releases/{env['RELEASE_SHA']}/{'0'*64}/{f['name']}.zip"} for f in manifest['functions']}
    from deploy import validate
    validate(manifest, runtime, env)
    if backend and origin(env['FC_FRONTEND_URL']) in (origin(env.get('PUBLIC_ORIGIN') or 'https://zlspace.clawworks.cn'), origin(env['CONTENT_ORIGIN'])):
        raise ValueError('FC_FRONTEND_URL must be the frontend function URL, not a gateway/content origin')
    return runtime


def main():
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['check', 'prepare', 'upload'])
    p.add_argument('--packages', default='fc-packages')
    p.add_argument('--runtime')
    args = p.parse_args()
    manifest = json.loads(Path('deploy/fc/functions.json').read_text())
    if args.command == 'check':
        check_settings(os.environ, manifest)
        print('Individual configuration validated; no cloud calls.')
        return
    if not args.runtime: raise ValueError('--runtime is required')
    if args.command == 'prepare':
        check_settings(os.environ, manifest)
        runtime = runtime_config(os.environ, any(f['name'] == 'game-service' for f in manifest['functions']))
        runtime['artifacts'] = package_index(args.packages, manifest, os.environ)
        # Caller uses mktemp; enforce 0600 even when a path is provided manually.
        descriptor = os.open(args.runtime, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w') as stream: json.dump(runtime, stream)
        print('Private runtime configuration prepared; values are not logged.')
    else:
        runtime = json.loads(Path(args.runtime).read_text())
        from deploy import validate
        validate(manifest, runtime, os.environ)
        upload(args.packages, runtime['artifacts'], os.environ)

if __name__ == '__main__':
    try: main()
    except ValueError as error:
        # Our validation errors contain field names only, never supplied values.
        raise SystemExit(str(error)) from None
    except Exception as error:
        raise SystemExit('FC package operation failed (' + type(error).__name__ + '); credentials omitted') from None
