#!/usr/bin/env python3
"""
火山引擎函数服务 — 一键部署脚本

用法:
  python scripts/deploy.py                  # 部署所有服务
  python scripts/deploy.py user-service     # 部署单个服务

所需环境变量（参考 .env.deploy.example）:
  必填: VOLCENGINE_ACCESS_KEY, VOLCENGINE_SECRET_KEY
  可选: VOLCENGINE_REGION (默认 cn-beijing)
        VOLCENGINE_API_HOST (默认 open.volcengineapi.com)
        VOLCENGINE_VPC_ID, VOLCENGINE_SUBNET_ID (不填则跳过 VPC 配置)
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

# ─── 从环境变量读取所有配置 ────────────────────────────────────────────────────
#
# VOLCENGINE_ACCESS_KEY / VOLCENGINE_SECRET_KEY
#   火山引擎控制台 → 访问控制 → API 访问密钥
#
# VOLCENGINE_REGION
#   部署目标地域，如 cn-beijing / cn-shanghai / cn-guangzhou
#   影响 HMAC 签名中的 region 字段，API 据此路由到对应地域的函数
#
# VOLCENGINE_API_HOST
#   火山引擎 OpenAPI 统一接入点，一般无需修改
#   默认: open.volcengineapi.com
#   所有地域共用同一个接入点，region 通过签名传递而非 URL 区分
#
# VOLCENGINE_VPC_ID / VOLCENGINE_SUBNET_ID
#   函数所在私有网络（VPC）和子网 ID
#   控制台 → 私有网络 → VPC 列表 → 复制 VPC ID
#   控制台 → 私有网络 → 子网列表 → 复制子网 ID
#   格式: vpc-xxxxxxxxxx / subnet-xxxxxxxxxx
#   不填则跳过 VPC 更新（保留控制台已设置的值）
#
AK        = os.environ.get("VOLCENGINE_ACCESS_KEY", "")
SK        = os.environ.get("VOLCENGINE_SECRET_KEY", "")
REGION    = os.environ.get("VOLCENGINE_REGION",   "cn-beijing")
API_HOST  = os.environ.get("VOLCENGINE_API_HOST", "open.volcengineapi.com")
VPC_ID    = os.environ.get("VOLCENGINE_VPC_ID",   "")
SUBNET_ID = os.environ.get("VOLCENGINE_SUBNET_ID","")

# VeFaaS API 版本（火山引擎函数服务接口版本，一般不需要修改）
VEFAAS_API_VERSION = "2021-04-30"


# ─── Volcengine HMAC-SHA256 签名 ──────────────────────────────────────────────

def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def volcengine_request(action: str, body: dict) -> dict:
    """
    调用火山引擎 VeFaaS OpenAPI。

    请求结构：
      POST https://{API_HOST}/?Action={action}&Version={VEFAAS_API_VERSION}
      Authorization: HMAC-SHA256 Credential={AK}/{date}/{REGION}/vefaas/request, ...
    """
    t          = datetime.datetime.utcnow()
    x_date     = t.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = t.strftime("%Y%m%d")

    payload      = json.dumps(body, separators=(",", ":"))
    payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    query             = f"Action={action}&Version={VEFAAS_API_VERSION}"
    canonical_headers = (
        f"content-type:application/json\n"
        f"host:{API_HOST}\n"
        f"x-content-sha256:{payload_hash}\n"
        f"x-date:{x_date}\n"
    )
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_req  = "\n".join([
        "POST", "/", query,
        canonical_headers, signed_headers, payload_hash,
    ])

    service          = "vefaas"
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
    resp = requests.post(
        f"https://{API_HOST}/?{query}",
        headers={
            "Content-Type":     "application/json",
            "X-Date":           x_date,
            "X-Content-Sha256": payload_hash,
            "Authorization":    authorization,
        },
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
    resp = volcengine_request("UpdateFunctionCode", {
        "FunctionName": name,
        "SourceType":   "Zip",
        "Code":         {"ZipFile": zip_b64},
    })
    err = resp.get("ResponseMetadata", {}).get("Error")
    if err:
        print(f"  ❌ 上传失败: {err.get('Code')} — {err.get('Message')}")
        return False
    print("  ✅ 代码上传成功")

    # 2. 更新配置（环境变量 + 可选 VPC）
    print("  ⚙️  更新配置...")
    config_body: dict = {
        "FunctionName": name,
        "EnvConf":      build_env(svc["port"]),
    }
    if VPC_ID and SUBNET_ID:
        config_body["VpcConfig"] = {"VpcId": VPC_ID, "SubnetId": SUBNET_ID}

    resp = volcengine_request("UpdateFunctionConfiguration", config_body)
    err = resp.get("ResponseMetadata", {}).get("Error")
    if err:
        print(f"  ❌ 配置更新失败: {err.get('Code')} — {err.get('Message')}")
        return False
    print("  ✅ 配置更新成功")
    return True


# ─── 入口 ──────────────────────────────────────────────────────────────────────

def main():
    if not AK or not SK:
        print("❌ 请设置 VOLCENGINE_ACCESS_KEY 和 VOLCENGINE_SECRET_KEY")
        print("   参考: cp .env.deploy.example .env.deploy && source .env.deploy")
        sys.exit(1)

    print(f"📍 部署配置:")
    print(f"   地域 (REGION):   {REGION}")
    print(f"   API 地址 (HOST): {API_HOST}")
    print(f"   VPC ID:          {VPC_ID  or '未设置（跳过 VPC 配置）'}")
    print(f"   Subnet ID:       {SUBNET_ID or '未设置（跳过 VPC 配置）'}")

    target   = sys.argv[1] if len(sys.argv) > 1 else "all"
    services = SERVICES if target == "all" else [s for s in SERVICES if s["svc"] == target]

    if not services:
        print(f"❌ 未知服务: {target}，可选: {[s['svc'] for s in SERVICES]} | all")
        sys.exit(1)

    print(f"\n🚀 开始部署 {len(services)} 个服务...")
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
