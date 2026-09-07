#!/usr/bin/env python3
"""Route public app-domain API paths directly to game-service at the FC edge.

Frontend nginx currently proxies /api and /admin to game-service through the FC
default trigger URL (FC_API_URL). Aliyun blocks external redirects on that domain
with ExternalRedirectForbidden. Sending these paths straight to game-service on
the custom domain removes the extra hop and avoids the platform error.
"""
import argparse
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


def public_domain_routes(prefix: str):
    game = f'{prefix}-game-service'
    front = f'{prefix}-frontend'
    return [
        ('/api/*', game, METHODS),
        ('/users/*', game, METHODS),
        ('/admin/*', game, METHODS),
        ('/games/*', game, METHODS),
        ('/game-shell/*', game, METHODS),
        ('/socket.io/*', game, METHODS),
        ('/9002299597e76e062cb56930926ed40c.txt', game, ['GET']),
        ('/33zqDBay4T.txt', game, ['GET']),
        ('/*', front, METHODS),
    ]


def build_route_config(prefix: str):
    routes = []
    for path, function_name, methods in public_domain_routes(prefix):
        routes.append(m.PathConfig(
            function_name=function_name,
            path=path,
            methods=methods,
            qualifier='LATEST',
        ))
    return m.RouteConfig(routes=routes)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--domain', default=os.environ.get('PUBLIC_APP_DOMAIN', 'www.zlspace.ai'))
    parser.add_argument('--prefix', default=os.environ.get('FC_PREFIX', 'gamevallies-prod'))
    parser.add_argument('--region', default=os.environ.get('FC_REGION', 'cn-hongkong'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()

    env = os.environ
    client = Client(Config(
        access_key_id=need(env, 'ALIBABA_CLOUD_ACCESS_KEY_ID'),
        access_key_secret=need(env, 'ALIBABA_CLOUD_ACCESS_KEY_SECRET'),
        security_token=env.get('ALIBABA_CLOUD_SECURITY_TOKEN') or None,
        endpoint=f'fcv3.{args.region}.aliyuncs.com',
        connect_timeout=10000,
        read_timeout=30000,
    ))

    existing = client.get_custom_domain(args.domain).body

    planned = [
        {'path': path, 'function': function_name, 'methods': methods}
        for path, function_name, methods in public_domain_routes(args.prefix)
    ]
    print(json.dumps({
        'domain': args.domain,
        'protocol': existing.protocol,
        'currentRoutes': [
            {'path': route.path, 'function': route.function_name}
            for route in (existing.route_config.routes or [])
        ],
        'plannedRoutes': planned,
    }, indent=2), flush=True)

    if not args.apply:
        print('Dry run only; pass --apply to update custom-domain routes.', flush=True)
        return

    client.update_custom_domain(
        args.domain,
        m.UpdateCustomDomainRequest(body=m.UpdateCustomDomainInput(
            route_config=build_route_config(args.prefix),
        )),
    )
    print(f'Updated FC custom-domain routes for {args.domain}.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except ValueError as error:
        raise SystemExit(str(error)) from None
