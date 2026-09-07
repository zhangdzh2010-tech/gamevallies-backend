#!/usr/bin/env python3
"""Restore www.zlspace.ai to frontend-only routing."""
import json
import os
import sys

from alibabacloud_fc20230330 import models as m
from alibabacloud_fc20230330.client import Client
from alibabacloud_tea_openapi.models import Config

METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']


def need(env, key):
    value = env.get(key, '').strip()
    if not value:
        raise ValueError(f'Missing {key}')
    return value


def main():
    domain = os.environ.get('PUBLIC_APP_DOMAIN', 'www.zlspace.ai')
    prefix = os.environ.get('FC_PREFIX', 'gamevallies-prod')
    region = os.environ.get('FC_REGION', 'cn-hongkong')
    client = Client(Config(
        access_key_id=need(os.environ, 'ALIBABA_CLOUD_ACCESS_KEY_ID'),
        access_key_secret=need(os.environ, 'ALIBABA_CLOUD_ACCESS_KEY_SECRET'),
        security_token=os.environ.get('ALIBABA_CLOUD_SECURITY_TOKEN') or None,
        endpoint=f'fcv3.{region}.aliyuncs.com',
    ))
    client.update_custom_domain(
        domain,
        m.UpdateCustomDomainRequest(body=m.UpdateCustomDomainInput(
            route_config=m.RouteConfig(routes=[
                m.PathConfig(
                    function_name=f'{prefix}-frontend',
                    path='/*',
                    methods=METHODS,
                    qualifier='LATEST',
                ),
            ]),
        )),
    )
    print(json.dumps({'domain': domain, 'routes': [{'path': '/*', 'function': f'{prefix}-frontend'}]}), flush=True)


if __name__ == '__main__':
    main()
