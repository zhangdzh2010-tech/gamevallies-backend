#!/usr/bin/env python3
"""
火山引擎函数服务 — 容器镜像部署脚本（官方 Python SDK）

流程: docker build → docker push → UpdateFunction(image) → Release

用法:
  python scripts/deploy.py                  # 部署所有服务
  python scripts/deploy.py user-service     # 部署单个服务

所需环境变量（参考 .env.deploy.example）:
  必填: VOLCENGINE_ACCESS_KEY, VOLCENGINE_SECRET_KEY
        VOLCENGINE_REGISTRY_NAMESPACE
  可选: VOLCENGINE_REGION             (默认 cn-shanghai)
        VOLCENGINE_REGISTRY           (默认 cr.volces.com)
        IMAGE_TAG                     (默认 latest；CI 中传入 github.sha)
        VOLCENGINE_VPC_ID, VOLCENGINE_SUBNET_ID, VOLCENGINE_SECURITY_GROUP_ID
"""

import os
import sys
import subprocess
import time
import warnings
warnings.filterwarnings("ignore")

import volcenginesdkvefaas
import volcenginesdkcore

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# svc key → function name in VeFaaS console (须与控制台函数名一致)
SERVICES = [
    {"svc": "user-service",   "name": "gv-user-service",   "port": 3001},
    {"svc": "game-service",   "name": "gv-game-service",   "port": 3002},
    {"svc": "feed-service",   "name": "gv-feed-service",   "port": 3004},
    {"svc": "ai-engine",      "name": "gv-ai-engine",      "port": 8000},
]

AK                = os.environ.get("VOLCENGINE_ACCESS_KEY",         "")
SK                = os.environ.get("VOLCENGINE_SECRET_KEY",         "")
REGION            = os.environ.get("VOLCENGINE_REGION",             "cn-shanghai")
REGISTRY          = os.environ.get("VOLCENGINE_REGISTRY",           "cr.volces.com")
NAMESPACE         = os.environ.get("VOLCENGINE_REGISTRY_NAMESPACE",  "")
IMAGE_TAG         = os.environ.get("IMAGE_TAG",                     "latest")
VPC_ID            = os.environ.get("VOLCENGINE_VPC_ID",             "")
SUBNET_ID         = os.environ.get("VOLCENGINE_SUBNET_ID",          "")
SECURITY_GROUP_ID = os.environ.get("VOLCENGINE_SECURITY_GROUP_ID",  "")


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
        volcenginesdkvefaas.EnvForUpdateFunctionInput(key="ADMIN_TOKEN",       value=os.environ.get("ADMIN_TOKEN", "admin123")),
    ]


def build_ai_envs() -> list:
    return [
        volcenginesdkvefaas.EnvForUpdateFunctionInput(key="LLM_API_KEY",    value=os.environ.get("LLM_API_KEY", "")),
        volcenginesdkvefaas.EnvForUpdateFunctionInput(key="LLM_BASE_URL",   value=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")),
        volcenginesdkvefaas.EnvForUpdateFunctionInput(key="LLM_MODEL",      value=os.environ.get("LLM_MODEL", "deepseek-chat")),
        volcenginesdkvefaas.EnvForUpdateFunctionInput(key="CORS_ORIGINS",   value='["*"]'),
        volcenginesdkvefaas.EnvForUpdateFunctionInput(key="PORT",           value="8000"),
    ]


def image_uri(svc_name: str) -> str:
    return f"{REGISTRY}/{NAMESPACE}/{svc_name}:{IMAGE_TAG}"


def shell(cmd: list) -> bool:
    """执行 shell 命令，实时输出，返回是否成功。"""
    result = subprocess.run(cmd, cwd=ROOT_DIR)
    return result.returncode == 0


def wait_image_sync(api: volcenginesdkvefaas.VEFAASApi, func_id: str, image: str, timeout: int = 300) -> bool:
    """等待 VeFaaS 镜像缓存就绪，最多 timeout 秒。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sync = api.get_image_sync_status(
                volcenginesdkvefaas.GetImageSyncStatusRequest(function_id=func_id, source=image))
            status = getattr(sync, "status", "") or ""
            cache_status = getattr(sync, "image_cache_status", "") or ""
            if cache_status.lower() == "ready" or status.lower() == "succeeded":
                print(f"  ✅ 镜像缓存就绪 (status={status})")
                return True
            if status.lower() == "failed":
                print(f"  ❌ 镜像同步失败，请检查 VCR 凭证")
                return False
            print(f"  ⏳ 等待镜像同步... status={status} cache={cache_status}")
        except Exception as e:
            print(f"  ⏳ 查询同步状态异常: {e}")
        time.sleep(10)
    print(f"  ❌ 等待镜像同步超时 ({timeout}秒)")
    return False


def deploy_service(api: volcenginesdkvefaas.VEFAASApi, svc: dict) -> bool:
    name  = svc["name"]
    image = image_uri(name)

    # 1. docker build
    print(f"  🔨 构建镜像: {image}")
    if svc["svc"] == "ai-engine":
        build_cmd = [
            "docker", "build",
            "--platform", "linux/amd64",
            "-t", image,
            os.path.join(ROOT_DIR, "packages", "ai-engine"),
        ]
    else:
        build_cmd = [
            "docker", "build",
            "--platform", "linux/amd64",
            "--build-arg", f"SERVICE={svc['svc']}",
            "--build-arg", f"PORT={svc['port']}",
            "-t", image,
            ROOT_DIR,
        ]
    if not shell(build_cmd):
        print(f"  ❌ docker build 失败")
        return False

    # 2. docker push
    print(f"  ⬆️  推送镜像到 VCR...")
    if not shell(["docker", "push", image]):
        print(f"  ❌ docker push 失败")
        return False
    print(f"  ✅ 镜像推送成功")

    # 3. 查找函数 ID
    print(f"  🔍 查找函数 {name}...")
    func_id = get_function_id(api, name)
    if not func_id:
        print(f"  ❌ 函数 {name} 不存在，请先在控制台创建（Webserver 模式，镜像源）")
        return False
    print(f"  ✅ 函数 ID: {func_id}")

    # 4. 更新函数（切换到镜像源 + 更新配置）
    print(f"  ⚙️  更新函数配置...")
    envs = build_envs(svc["port"]) if svc["svc"] != "ai-engine" else build_ai_envs()
    update_req = volcenginesdkvefaas.UpdateFunctionRequest(
        id=func_id,
        source_type="image",
        source=image,
        source_access_config=volcenginesdkvefaas.SourceAccessConfigForUpdateFunctionInput(
            username=os.environ.get("VOLCENGINE_REGISTRY_USERNAME", AK),
            password=os.environ.get("VOLCENGINE_REGISTRY_PASSWORD", SK),
        ),
        envs=envs,
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

    # 5. 等待镜像缓存就绪
    print(f"  ⏳ 等待镜像缓存就绪...")
    if not wait_image_sync(api, func_id, image):
        return False

    # 6. 发布新版本（revision_number=0 = 当前最新草稿）
    print(f"  🚀 发布新版本...")
    try:
        rel = api.release(volcenginesdkvefaas.ReleaseRequest(function_id=func_id, revision_number=0))
        print(f"  ✅ 发布成功（版本号: {rel.new_revision_number}，状态: {rel.status}）")
    except Exception as e:
        print(f"  ❌ 发布失败: {e}")
        return False

    return True


def main():
    if not AK or not SK:
        print("❌ 请设置 VOLCENGINE_ACCESS_KEY 和 VOLCENGINE_SECRET_KEY")
        sys.exit(1)
    if not NAMESPACE:
        print("❌ 请设置 VOLCENGINE_REGISTRY_NAMESPACE（镜像仓库命名空间）")
        print("   控制台 → 容器镜像服务 → 命名空间 → 创建后填入此变量")
        sys.exit(1)

    print(f"📍 部署配置:")
    print(f"   地域:             {REGION}")
    print(f"   镜像仓库:         {REGISTRY}/{NAMESPACE}")
    print(f"   镜像 Tag:         {IMAGE_TAG}")
    print(f"   VPC ID:           {VPC_ID or '未设置'}")
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
