#!/usr/bin/env python3
"""
火山引擎函数服务 — 一键部署脚本

用法:
  python scripts/deploy.py                  # 部署所有服务
  python scripts/deploy.py user-service     # 部署单个服务

所需环境变量（参考 .env.deploy.example）:
  VOLCENGINE_ACCESS_KEY / VOLCENGINE_SECRET_KEY
  DATABASE_URL / REDIS_URL
  JWT_SECRET / JWT_REFRESH_SECRET
"""

import os
import sys
import json
import base64
import hashlib
import hmac
import datetime
import requests

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST_DIR = os.path.join(ROOT_DIR, ".lambda-dist")

SERVICES = [
    {"svc": "user-service",   "name": "gamevallies-user-service",   "port": 3001},
    {"svc": "game-service",   "name": "gamevallies-game-service",   "port": 3002},
    {"svc": "social-service", "name": "gamevallies-social-service", "port": 3003},
    {"svc": "feed-service",   "name": "gamevallies-feed-service",   "port": 3004},
]

AK     = os.environ.get("VOLCENGINE_ACCESS_KEY", "")
SK     = os.environ.get("VOLCENGINE_SECRET_KEY", "")
REGION = os.environ.get("VOLCENGINE_REGION", "cn-beijing")


# ─── Volcengine HMAC-SHA256 签名 ──────────────────────────────────────────────

def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def volcengine_request(action: str, version: str, body: dict) -> dict:
    host    = "open.volcengineapi.com"
    service = "vefaas"

    t          = datetime.datetime.utcnow()
    x_date     = t.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = t.strftime("%Y%m%d")

    payload      = json.dumps(body, separators=(",", ":"))
    payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    query             = f"Action={action}&Version={version}"
    canonical_headers = (
        f"content-type:application/json\n"
        f"host:{host}\n"
        f"x-content-sha256:{payload_hash}\n"
        f"x-date:{x_date}\n"
    )
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_req  = "\n".join([
        "POST", "/", query,
        canonical_headers, signed_headers, payload_hash,
    ])

    credential_scope = f"{date_stamp}/{REGION}/{service}/request"
    string_to_sign   = "\n".join([
        "HMAC-SHA256", x_date, credential_scope,
        hashlib.sha256(canonical_req.encode("utf-8")).hexdigest(),
    ])

    k_date    = _sign(("VOLC" + SK).encode("utf-8"), date_stamp)
    k_region  = _sign(k_date,    REGION)
    k_service = _sign(k_region,  service)
    k_signing = _sign(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"HMAC-SHA256 Credential={AK}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    headers = {
        "Content-Type":    "application/json",
        "X-Date":          x_date,
        "X-Content-Sha256": payload_hash,
        "Authorization":   authorization,
    }
    resp = requests.post(
        f"https://{host}/?{query}",
        headers=headers,
        data=payload,
        timeout=120,
    )
    return resp.json()


# ─── 环境变量构造 ──────────────────────────────────────────────────────────────

def build_env(port: int) -> list:
    return [
        {"Key": "NODE_ENV",           "Value": "production"},
        {"Key": "PORT",               "Value": str(port)},
        {"Key": "DATABASE_URL",       "Value": os.environ.get("DATABASE_URL", "")},
        {"Key": "REDIS_URL",          "Value": os.environ.get("REDIS_URL", "")},
        {"Key": "JWT_SECRET",         "Value": os.environ.get("JWT_SECRET", "")},
        {"Key": "JWT_REFRESH_SECRET", "Value": os.environ.get("JWT_REFRESH_SECRET", "")},
        {"Key": "CORS_ORIGIN",        "Value": os.environ.get("CORS_ORIGIN", "*")},
    ]


# ─── 部署单个服务 ──────────────────────────────────────────────────────────────

def deploy_service(svc: dict) -> bool:
    name     = svc["name"]
    zip_path = os.path.join(DIST_DIR, f"{svc['svc']}.zip")

    if not os.path.exists(zip_path):
        print(f"  ❌ 找不到 zip: {zip_path}，请先运行 npm run lambda:build")
        return False

    size_mb = os.path.getsize(zip_path) // 1024 // 1024
    print(f"  📦 读取 zip（{size_mb} MB）...")
    with open(zip_path, "rb") as f:
        zip_b64 = base64.b64encode(f.read()).decode("utf-8")

    # 1. 上传代码
    print("  🚀 上传代码...")
    resp = volcengine_request("UpdateFunctionCode", "2021-04-30", {
        "FunctionName": name,
        "SourceType":   "Zip",
        "Code":         {"ZipFile": zip_b64},
    })
    err = resp.get("ResponseMetadata", {}).get("Error")
    if err:
        print(f"  ❌ 上传失败: {err.get('Code')} — {err.get('Message')}")
        return False
    print("  ✅ 代码上传成功")

    # 2. 更新环境变量
    print("  ⚙️  更新环境变量...")
    resp = volcengine_request("UpdateFunctionConfiguration", "2021-04-30", {
        "FunctionName": name,
        "EnvConf":      build_env(svc["port"]),
    })
    err = resp.get("ResponseMetadata", {}).get("Error")
    if err:
        print(f"  ❌ 配置更新失败: {err.get('Code')} — {err.get('Message')}")
        return False
    print("  ✅ 环境变量更新成功")
    return True


# ─── 入口 ──────────────────────────────────────────────────────────────────────

def main():
    if not AK or not SK:
        print("❌ 请设置 VOLCENGINE_ACCESS_KEY 和 VOLCENGINE_SECRET_KEY")
        print("   参考: cp .env.deploy.example .env.deploy && source .env.deploy")
        sys.exit(1)

    target   = sys.argv[1] if len(sys.argv) > 1 else "all"
    services = SERVICES if target == "all" else [s for s in SERVICES if s["svc"] == target]

    if not services:
        print(f"❌ 未知服务: {target}，可选: {[s['svc'] for s in SERVICES]} | all")
        sys.exit(1)

    print(f"\n🚀 开始部署（区域: {REGION}）")
    failed = []
    for svc in services:
        print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"🔨 {svc['name']}")
        if not deploy_service(svc):
            failed.append(svc["name"])

    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if failed:
        print(f"❌ 部署失败: {failed}")
        sys.exit(1)
    print("🎉 所有服务部署完成！")


if __name__ == "__main__":
    main()
