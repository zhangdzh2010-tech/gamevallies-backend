#!/usr/bin/env python3
"""
火山引擎函数服务 — 一键部署脚本（官方 Python SDK）

流程: 本地 zip → GetCodeUploadAddress → PUT 上传 → UpdateFunction(配置) → Release

用法:
  python scripts/deploy.py                  # 部署所有服务
  python scripts/deploy.py user-service     # 部署单个服务

所需环境变量（参考 .env.deploy.example）:
  必填: VOLCENGINE_ACCESS_KEY, VOLCENGINE_SECRET_KEY
  可选: VOLCENGINE_REGION (默认 cn-shanghai)
        VOLCENGINE_VPC_ID, VOLCENGINE_SUBNET_ID
"""

import os
import sys
import warnings
warnings.filterwarnings("ignore")

import requests
import volcenginesdkvefaas
import volcenginesdkcore

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST_DIR = os.path.join(ROOT_DIR, ".lambda-dist")

# svc key → function name in VeFaaS console (须与控制台函数名一致)
SERVICES = [
    {"svc": "user-service",   "name": "gv-user-service",   "port": 3001},
    {"svc": "game-service",   "name": "gv-game-service",   "port": 3002},
    {"svc": "social-service", "name": "gv-social-service", "port": 3003},
    {"svc": "feed-service",   "name": "gv-feed-service",   "port": 3004},
]

AK               = os.environ.get("VOLCENGINE_ACCESS_KEY",       "")
SK               = os.environ.get("VOLCENGINE_SECRET_KEY",       "")
REGION           = os.environ.get("VOLCENGINE_REGION",           "cn-shanghai")
VPC_ID           = os.environ.get("VOLCENGINE_VPC_ID",           "")
SUBNET_ID        = os.environ.get("VOLCENGINE_SUBNET_ID",        "")
SECURITY_GROUP_ID = os.environ.get("VOLCENGINE_SECURITY_GROUP_ID", "")


def get_api() -> volcenginesdkvefaas.VEFAASApi:
    cfg = volcenginesdkcore.Configuration()
    cfg.ak = AK
    cfg.sk = SK
    cfg.region = REGION
    return volcenginesdkvefaas.VEFAASApi(volcenginesdkcore.ApiClient(cfg))


def get_function_id(api: volcenginesdkvefaas.VEFAASApi, name: str) -> str:
    resp = api.list_functions(volcenginesdkvefaas.ListFunctionsRequest(page_size=100))
    if resp.items:
        for f in resp.items:
            if f.name == name:
                return f.id
    return ""


def build_envs(port: int) -> list:
    return [
        volcenginesdkvefaas.EnvForUpdateFunctionInput(key="NODE_ENV",           value="production"),
        volcenginesdkvefaas.EnvForUpdateFunctionInput(key="PORT",               value=str(port)),
        volcenginesdkvefaas.EnvForUpdateFunctionInput(key="DATABASE_URL",       value=os.environ.get("DATABASE_URL", "")),
        volcenginesdkvefaas.EnvForUpdateFunctionInput(key="REDIS_URL",          value=os.environ.get("REDIS_URL", "")),
        volcenginesdkvefaas.EnvForUpdateFunctionInput(key="JWT_SECRET",         value=os.environ.get("JWT_SECRET", "")),
        volcenginesdkvefaas.EnvForUpdateFunctionInput(key="JWT_REFRESH_SECRET", value=os.environ.get("JWT_REFRESH_SECRET", "")),
        volcenginesdkvefaas.EnvForUpdateFunctionInput(key="CORS_ORIGIN",        value=os.environ.get("CORS_ORIGIN", "*")),
    ]


def deploy_service(api: volcenginesdkvefaas.VEFAASApi, svc: dict) -> bool:
    name     = svc["name"]
    zip_path = os.path.join(DIST_DIR, f"{svc['svc']}.zip")

    if not os.path.exists(zip_path):
        print(f"  ❌ 找不到 zip: {zip_path}，请先运行 npm run lambda:build")
        return False

    file_size = os.path.getsize(zip_path)
    size_mb   = file_size // 1024 // 1024

    # 1. 查找函数 ID
    print(f"  🔍 查找函数 {name}...")
    func_id = get_function_id(api, name)
    if not func_id:
        print(f"  ❌ 函数 {name} 不存在，请先在控制台创建函数（Webserver 模式，Node.js 20）")
        return False
    print(f"  ✅ 函数 ID: {func_id}")

    # 2. 获取代码上传地址（预签名 PUT URL）
    print(f"  ☁️  获取上传地址（{size_mb} MB）...")
    try:
        upload_resp = api.get_code_upload_address(
            volcenginesdkvefaas.GetCodeUploadAddressRequest(
                function_id=func_id,
                content_length=file_size,
            )
        )
    except Exception as e:
        print(f"  ❌ 获取上传地址失败: {e}")
        return False
    upload_url = upload_resp.upload_address
    if not upload_url:
        print(f"  ❌ 获取上传地址失败: 响应为空")
        return False

    # 3. 上传 zip 包
    print(f"  ⬆️  上传代码...")
    try:
        with open(zip_path, "rb") as f:
            put_resp = requests.put(
                upload_url,
                data=f,
                headers={"Content-Type": "application/zip"},
                timeout=300,
            )
        if put_resp.status_code not in (200, 204):
            print(f"  ❌ 上传失败: HTTP {put_resp.status_code} {put_resp.text[:200]}")
            return False
    except Exception as e:
        print(f"  ❌ 上传失败: {e}")
        return False
    print(f"  ✅ 代码上传成功")

    # 4. 更新函数配置（环境变量、VPC，不变更代码）
    print(f"  ⚙️  更新函数配置...")
    update_req = volcenginesdkvefaas.UpdateFunctionRequest(
        id=func_id,
        envs=build_envs(svc["port"]),
    )
    if VPC_ID and SUBNET_ID and SECURITY_GROUP_ID:
        update_req.vpc_config = volcenginesdkvefaas.VpcConfigForUpdateFunctionInput(
            enable_vpc=True,
            vpc_id=VPC_ID,
            subnet_ids=[SUBNET_ID],
            security_group_ids=[SECURITY_GROUP_ID],
        )
    try:
        api.update_function(update_req)
    except Exception as e:
        print(f"  ❌ 更新配置失败: {e}")
        return False
    print(f"  ✅ 配置更新成功")

    # 5. 发布新版本（revision_number=0 表示当前最新草稿）
    print(f"  🚀 发布新版本...")
    try:
        rel = api.release(volcenginesdkvefaas.ReleaseRequest(function_id=func_id, revision_number=0))
        print(f"  ✅ 发布成功（新版本号: {rel.new_revision_number}，状态: {rel.status}）")
    except Exception as e:
        print(f"  ❌ 发布失败: {e}")
        return False

    return True


def main():
    if not AK or not SK:
        print("❌ 请设置 VOLCENGINE_ACCESS_KEY 和 VOLCENGINE_SECRET_KEY")
        sys.exit(1)

    print(f"📍 部署配置:")
    print(f"   地域:             {REGION}")
    print(f"   VPC ID:           {VPC_ID  or '未设置'}")
    print(f"   Subnet ID:        {SUBNET_ID or '未设置'}")
    print(f"   Security Group:   {SECURITY_GROUP_ID or '未设置（跳过 VPC 配置）'}")

    target   = sys.argv[1] if len(sys.argv) > 1 else "all"
    services = SERVICES if target == "all" else [s for s in SERVICES if s["svc"] == target]

    if not services:
        print(f"❌ 未知服务: {target}，可选: {[s['svc'] for s in SERVICES]} | all")
        sys.exit(1)

    api = get_api()

    print(f"\n🚀 开始部署 {len(services)} 个服务...")
    failed = []
    for svc in services:
        print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"🔨 {svc['name']}")
        if not deploy_service(api, svc):
            failed.append(svc["name"])

    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if failed:
        print(f"❌ 部署失败: {failed}")
        sys.exit(1)
    print("🎉 所有服务部署完成！")


if __name__ == "__main__":
    main()
