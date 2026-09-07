#!/usr/bin/env python3
"""Point frontend nginx API_UPSTREAM at the public app domain instead of fcapp.run."""
import json
import os
import sys

from alibabacloud_fc20230330 import models as m
from alibabacloud_fc20230330.client import Client
from alibabacloud_tea_openapi.models import Config


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', default=os.environ.get('FC_API_PROXY_ORIGIN', 'https://www.zlspace.ai'))
    parser.add_argument('--prefix', default=os.environ.get('FC_PREFIX', 'gamevallies-prod'))
    parser.add_argument('--region', default=os.environ.get('FC_REGION', 'cn-hongkong'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()

    upstream = args.upstream.rstrip('/')
    if not upstream.startswith('https://'):
        raise ValueError('Expected HTTPS upstream origin')

    client = Client(Config(
        access_key_id=os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'],
        access_key_secret=os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET'],
        security_token=os.environ.get('ALIBABA_CLOUD_SECURITY_TOKEN') or None,
        endpoint=f'fcv3.{args.region}.aliyuncs.com',
    ))

    name = f'{args.prefix}-frontend'
    fn = client.get_function(name, m.GetFunctionRequest()).body
    env = dict(fn.environment_variables or {})
    current = env.get('API_UPSTREAM', '')
    print(json.dumps({'function': name, 'currentUpstream': current, 'plannedUpstream': upstream}, indent=2), flush=True)
    if current == upstream:
        print('Already up to date.', flush=True)
        return
    if not args.apply:
        print('Dry run only; pass --apply to update frontend API_UPSTREAM.', flush=True)
        return
    env['API_UPSTREAM'] = upstream
    client.update_function(name, m.UpdateFunctionRequest(body=m.UpdateFunctionInput(
        environment_variables=env,
    )))
    print(f'Updated {name} API_UPSTREAM to {upstream}.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except KeyError as error:
        raise SystemExit(f'Missing environment variable: {error}') from None
