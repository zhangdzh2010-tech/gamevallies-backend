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
import time
import subprocess
import warnings
warnings.filterwarnings("ignore")

import volcenginesdkvefaas
import volcenginesdkcore

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# svc key → function name in VeFaaS console (须与控制台函数名一致)
# type="python" 表示 AI 引擎（独立 Dockerfile，不同构建参数）
SERVICES = [
    {"svc": "user-service",   "name": "gv-user-service",   "port": 3001, "internet": True},
    {"svc": "game-service",   "name": "gv-game-service",   "port": 3002},
    # social-service 已合入 feed-service（节省函数配额）
    {"svc": "feed-service",   "name": "gv-feed-service",   "port": 3004},
    # type="python": 独立 Dockerfile; internet=True: 需要访问公网（DeepSeek API）
    {"svc": "ai-engine", "name": "gv-ai-engine", "port": 8000, "type": "python", "internet": True},
]

AK                = os.environ.get("VOLCENGINE_ACCESS_KEY",         "")
SK                = os.environ.get("VOLCENGINE_SECRET_KEY",         "")
REGION            = os.environ.get("VOLCENGINE_REGION",             "cn-shanghai")
REGISTRY          = os.environ.get("VOLCENGINE_REGISTRY",           "gamevallies-repo-cn-shanghai.cr.volces.com")
NAMESPACE         = os.environ.get("VOLCENGINE_REGISTRY_NAMESPACE",  "")
IMAGE_TAG         = os.environ.get("IMAGE_TAG",                     "latest")
VCR_USERNAME      = os.environ.get("VOLCENGINE_REGISTRY_USERNAME",  "")
VCR_PASSWORD      = os.environ.get("VOLCENGINE_REGISTRY_PASSWORD",  "")
VPC_ID            = os.environ.get("VOLCENGINE_VPC_ID",             "")
SUBNET_ID         = os.environ.get("VOLCENGINE_SUBNET_ID",          "")
SECURITY_GROUP_ID = os.environ.get("VOLCENGINE_SECURITY_GROUP_ID",  "")
AI_ENGINE_URL     = os.environ.get("AI_ENGINE_URL",                 "")
GAME_SERVICE_URL  = os.environ.get("GAME_SERVICE_URL",              "")


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


def _env_vars(port: int, svc: str = "") -> dict:
    """NestJS 服务公共环境变量"""
    env = {
        "NODE_ENV":           "production",
        "PORT":               str(port),
        "DATABASE_URL":       os.environ.get("DATABASE_URL", ""),
        "REDIS_URL":          os.environ.get("REDIS_URL", ""),
        "JWT_SECRET":         os.environ.get("JWT_SECRET", ""),
        "JWT_REFRESH_SECRET": os.environ.get("JWT_REFRESH_SECRET", ""),
        "CORS_ORIGIN":        os.environ.get("CORS_ORIGIN", "*"),
        "ADMIN_TOKEN":        os.environ.get("ADMIN_TOKEN", "admin123"),
    }
    if AI_ENGINE_URL:
        env["AI_ENGINE_URL"] = AI_ENGINE_URL
    if GAME_SERVICE_URL:
        env["APP_URL"] = GAME_SERVICE_URL

    # 阿里云短信 Dysmsapi（仅 user-service 需要）
    if svc == "user-service":
        for key in [
            "ALIYUN_ACCESS_KEY_ID", "ALIYUN_ACCESS_KEY_SECRET",
            "ALIYUN_SMS_REGION_ID", "ALIYUN_SMS_SIGN_NAME",
            "ALIYUN_SMS_TPL_REGISTER", "ALIYUN_SMS_TPL_LOGIN",
            "VERIFY_CODE_SEND_INTERVAL_SECONDS",
        ]:
            val = os.environ.get(key, "")
            if val:
                env[key] = val

    return env


def _ai_env_vars(port: int) -> dict:
    """AI 引擎（Python）环境变量"""
    return {
        "ENVIRONMENT":                   "production",
        "PORT":                          str(port),
        "DATABASE_URL":                  os.environ.get("DATABASE_URL", ""),
        "REDIS_URL":                     os.environ.get("REDIS_URL", ""),
        "LLM_MODE":                      os.environ.get("LLM_MODE", "real"),
        "LLM_API_KEY":                   os.environ.get("LLM_API_KEY", ""),
        "LLM_BASE_URL":                  os.environ.get("LLM_BASE_URL", "https://api.minimaxi.com/v1"),
        "LLM_MODEL":                     os.environ.get("LLM_MODEL", "MiniMax-M2.5"),
        "LLM_FAST_MODEL":                os.environ.get("LLM_FAST_MODEL", "MiniMax-M2.5"),
        "CORS_ORIGINS":                  '["*"]',
        "TEMPLATE_CONFIDENCE_THRESHOLD": os.environ.get("TEMPLATE_CONFIDENCE_THRESHOLD", "0.8"),
        "HYBRID_CONFIDENCE_THRESHOLD":   os.environ.get("HYBRID_CONFIDENCE_THRESHOLD", "0.5"),
        "QA_MAX_RETRIES":                os.environ.get("QA_MAX_RETRIES", "3"),
        "PIPELINE_TIMEOUT_S":            os.environ.get("PIPELINE_TIMEOUT_S", "600"),
    }


def build_envs_update(port: int, ai: bool = False, svc: str = "") -> list:
    src = _ai_env_vars(port) if ai else _env_vars(port, svc=svc)
    return [
        volcenginesdkvefaas.EnvForUpdateFunctionInput(key=k, value=v)
        for k, v in src.items()
    ]


def build_envs_create(port: int, ai: bool = False, svc: str = "") -> list:
    src = _ai_env_vars(port) if ai else _env_vars(port, svc=svc)
    return [
        volcenginesdkvefaas.EnvForCreateFunctionInput(key=k, value=v)
        for k, v in src.items()
    ]


def image_uri(svc_name: str) -> str:
    return f"{REGISTRY}/{NAMESPACE}/{svc_name}:{IMAGE_TAG}"


def shell(cmd: list) -> bool:
    """执行 shell 命令，实时输出，返回是否成功。"""
    result = subprocess.run(cmd, cwd=ROOT_DIR)
    return result.returncode == 0


def _svc_command(svc: dict) -> str:
    """返回函数的启动命令（覆盖 /opt/application/run.sh）

    VeFaaS 用 sh -c 执行命令，PATH 可能不含 /usr/local/bin，
    因此 node 和 python 必须使用绝对路径。
    """
    if svc.get("type") == "python":
        return f"/usr/local/bin/python -m uvicorn src.main:app --host 0.0.0.0 --port {svc['port']}"
    return f"/usr/local/bin/node /app/packages/{svc['svc']}/dist/main.js"


def _vpc_config_create(svc: dict):
    """构造 VPC 配置（CreateFunction 用）"""
    if not (VPC_ID and SUBNET_ID and SECURITY_GROUP_ID):
        return None
    return volcenginesdkvefaas.VpcConfigForCreateFunctionInput(
        enable_vpc=True,
        vpc_id=VPC_ID,
        subnet_ids=[SUBNET_ID],
        security_group_ids=[SECURITY_GROUP_ID],
        enable_shared_internet_access=svc.get("internet", False),
    )


def _vpc_config_update(svc: dict):
    """构造 VPC 配置（UpdateFunction 用）"""
    if not (VPC_ID and SUBNET_ID and SECURITY_GROUP_ID):
        return None
    return volcenginesdkvefaas.VpcConfigForUpdateFunctionInput(
        enable_vpc=True,
        vpc_id=VPC_ID,
        subnet_ids=[SUBNET_ID],
        security_group_ids=[SECURITY_GROUP_ID],
        enable_shared_internet_access=svc.get("internet", False),
    )


def create_function(api: volcenginesdkvefaas.VEFAASApi, svc: dict, image: str) -> str:
    """创建新函数，返回函数 ID。失败返回空字符串。"""
    name = svc["name"]
    is_ai = svc.get("type") == "python"
    print(f"  ➕ 函数不存在，自动创建 {name}...")
    create_req = volcenginesdkvefaas.CreateFunctionRequest(
        name=name,
        runtime="native/v1",
        source_type="image",
        source=image,
        source_access_config=volcenginesdkvefaas.SourceAccessConfigForCreateFunctionInput(
            username=VCR_USERNAME or AK,
            password=VCR_PASSWORD or SK,
        ),
        port=svc["port"],
        command=_svc_command(svc),
        envs=build_envs_create(svc["port"], ai=is_ai, svc=svc["svc"]),
    )
    vpc = _vpc_config_create(svc)
    if vpc:
        create_req.vpc_config = vpc
    try:
        resp = api.create_function(create_req)
        print(f"  ✅ 函数创建成功（ID: {resp.id}）")
        return resp.id
    except Exception as e:
        print(f"  ❌ 创建函数失败: {e}")
        return ""


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
    name   = svc["name"]
    image  = image_uri(name)
    is_ai  = svc.get("type") == "python"

    # 1. docker build
    print(f"  🔨 构建镜像: {image}")
    if is_ai:
        # AI 引擎使用自己的 Dockerfile，构建上下文是 packages/ai-engine/
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

    # 3. 查找或创建函数
    print(f"  🔍 查找函数 {name}...")
    func_id = get_function_id(api, name)
    if func_id:
        print(f"  ✅ 函数已存在（ID: {func_id}），执行更新...")
        # 4a. 更新已有函数
        update_req = volcenginesdkvefaas.UpdateFunctionRequest(
            id=func_id,
            source_type="image",
            source=image,
            source_access_config=volcenginesdkvefaas.SourceAccessConfigForUpdateFunctionInput(
                username=VCR_USERNAME or AK,
                password=VCR_PASSWORD or SK,
            ),
            command=_svc_command(svc),
            envs=build_envs_update(svc["port"], ai=is_ai, svc=svc["svc"]),
        )
        vpc = _vpc_config_update(svc)
        if vpc:
            update_req.vpc_config = vpc
        try:
            api.update_function(update_req)
        except Exception as e:
            print(f"  ❌ 更新配置失败: {e}")
            return False
        print(f"  ✅ 配置更新成功")
    else:
        # 4b. 创建新函数（已包含镜像、envs、vpc 等全部配置）
        func_id = create_function(api, svc, image)
        if not func_id:
            return False

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
