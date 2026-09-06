#!/usr/bin/env python3
"""Read-only FC diagnostics. Never emit function environment variables or raw errors."""
import json
import os
import re
from alibabacloud_fc20230330.client import Client
from alibabacloud_fc20230330 import models as m
from alibabacloud_tea_openapi.models import Config

def identifier(value):
    text = str(value or '')
    return text if re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', text) else None

def reason_summary(value):
    # Provider error strings can embed application logs and credentials.
    # Extract known diagnostic markers only; do not print the original text.
    text = str(value or '')
    markers = [
        'ModuleNotFoundError', 'ImportError', 'PermissionError', 'Permission denied',
        'No such file or directory', 'Exec format error', 'GLIBC',
        'OutOfMemory', 'OOM', 'MemoryError', 'HealthCheck', 'health check',
        'Initialization', 'initialization', 'timeout', 'Timeout', 'Connection refused',
        'ConnectionError', 'AuthenticationError', 'WRONGPASS', 'NOAUTH',
        'Redis', 'redis', 'Insufficient', 'Quota', 'ResourceExhausted',
        'AccessDenied', 'InvalidArgument', 'InvalidAccessKeyId',
        'FC_INTERNAL_TOKEN', 'ValidationError', 'SyntaxError',
    ]
    return {'present': bool(text), 'markers': [x for x in markers if x in text],
            'exceptionTypes': sorted(set(re.findall(r'\b([A-Za-z]{2,40}(?:Error|Exception))\b', text)))[:12]}

def run(client, name):
    queries = {
        'function': lambda: client.get_function(name, m.GetFunctionRequest()).body.to_map(),
        'provision': lambda: client.get_provision_config(name, m.GetProvisionConfigRequest(qualifier='LATEST')).body.to_map(),
    }
    for kind, query in queries.items():
        try:
            raw = query()
            if kind == 'function':
                keys = ['runtime', 'state', 'lastUpdateStatus', 'disableOndemand', 'cpu', 'memorySize', 'instanceConcurrency', 'codeSize']
                result = {k: raw.get(k) for k in keys}
                for k in ['stateReason', 'lastUpdateStatusReason']:
                    result[k] = reason_summary(raw.get(k))
                for k in ['stateReasonCode', 'lastUpdateStatusReasonCode']:
                    result[k] = identifier(raw.get(k))
            else:
                result = {k: raw.get(k) for k in ['current', 'target', 'defaultTarget', 'alwaysAllocateCPU']}
                result['currentError'] = reason_summary(raw.get('currentError'))
            print(json.dumps({'function': name, 'query': kind, 'result': result}), flush=True)
        except Exception as error:
            print(json.dumps({'function': name, 'query': kind,
                'errorType': identifier(type(error).__name__),
                'code': identifier(getattr(error, 'code', None)),
                'status': identifier(getattr(error, 'status_code', None))}), flush=True)

def main():
    region = os.environ['FC_REGION']
    prefix = os.environ['FC_PREFIX']
    if not re.fullmatch(r'[a-z0-9-]+', region) or not re.fullmatch(r'[a-z0-9-]+', prefix):
        raise ValueError('Invalid region or prefix')
    cfg = Config(access_key_id=os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'],
                 access_key_secret=os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET'],
                 security_token=os.getenv('ALIBABA_CLOUD_SECURITY_TOKEN') or None)
    cfg.endpoint = 'fcv3.' + region + '.aliyuncs.com'
    cfg.connect_timeout, cfg.read_timeout = 10000, 20000
    client = Client(cfg)
    for suffix in ['ai-engine', 'game-service', 'content', 'db-migrate']:
        run(client, prefix + '-' + suffix)

if __name__ == '__main__':
    main()
