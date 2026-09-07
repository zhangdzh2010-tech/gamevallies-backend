#!/usr/bin/env python3
"""Recycle provisioned FC instances so nginx picks up new environment variables."""
import json
import os
import sys
import time

from alibabacloud_fc20230330 import models as m
from alibabacloud_fc20230330.client import Client
from alibabacloud_tea_openapi.models import Config


def client_from_env():
    region = os.environ.get('FC_REGION', 'cn-hongkong')
    return Client(Config(
        access_key_id=os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'],
        access_key_secret=os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET'],
        security_token=os.environ.get('ALIBABA_CLOUD_SECURITY_TOKEN') or None,
        endpoint=f'fcv3.{region}.aliyuncs.com',
        connect_timeout=10000,
        read_timeout=30000,
    ))


def recycle(fc, name, target):
    print(json.dumps({'function': name, 'action': 'scale_to_zero'}), flush=True)
    fc.put_provision_config(name, m.PutProvisionConfigRequest(
        qualifier='LATEST',
        body=m.PutProvisionConfigInput(default_target=0),
    ))
    for _ in range(60):
        state = fc.get_provision_config(name, m.GetProvisionConfigRequest(qualifier='LATEST')).body
        if state.current == 0 and state.target == 0:
            break
        time.sleep(2)
    else:
        raise TimeoutError(f'{name} did not scale to zero')

    print(json.dumps({'function': name, 'action': 'restore', 'target': target}), flush=True)
    fc.put_provision_config(name, m.PutProvisionConfigRequest(
        qualifier='LATEST',
        body=m.PutProvisionConfigInput(default_target=target, always_allocate_cpu=True),
    ))
    for _ in range(90):
        state = fc.get_provision_config(name, m.GetProvisionConfigRequest(qualifier='LATEST')).body
        if not state.current_error and state.current == target and state.target == target:
            print(json.dumps({
                'function': name,
                'current': state.current,
                'target': state.target,
                'ready': True,
            }), flush=True)
            return
        time.sleep(3)
    raise TimeoutError(f'{name} provisioning did not recover')


def patch_env(fc, name, updates):
    fn = fc.get_function(name, m.GetFunctionRequest()).body
    env = dict(fn.environment_variables or {})
    env.update(updates)
    fc.update_function(name, m.UpdateFunctionRequest(body=m.UpdateFunctionInput(
        environment_variables=env,
    )))


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--prefix', default=os.environ.get('FC_PREFIX', 'gamevallies-prod'))
    parser.add_argument('--frontend-target', type=int, default=1)
    args = parser.parse_args()

    fc = client_from_env()
    prefix = args.prefix
    frontend = f'{prefix}-frontend'
    game = f'{prefix}-game-service'

    patch_env(fc, frontend, {'API_UPSTREAM': 'https://www.zlspace.ai'})
    patch_env(fc, game, {'PUBLIC_APP_HOSTS': 'www.zlspace.ai,api.zlspace.ai'})
    recycle(fc, frontend, args.frontend_target)


if __name__ == '__main__':
    try:
        main()
    except KeyError as error:
        raise SystemExit(f'Missing environment variable: {error}') from None
