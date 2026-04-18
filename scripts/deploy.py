#!/usr/bin/env python3
from __future__ import annotations
"""
火山引擎函数服务 — 容器镜像部署脚本（官方 Python SDK）

流程: docker build → docker push → UpdateFunction(image) → Release

用法:
  python scripts/deploy.py                  # 部署所有服务
  python scripts/deploy.py user-service     # 部署单个服务
  python scripts/deploy.py ai-engine-cn     # 只部署中国区 ai-engine

所需环境变量（参考 .env.deploy.example）:
  必填: VOLCENGINE_ACCESS_KEY, VOLCENGINE_SECRET_KEY
        VOLCENGINE_REGISTRY_NAMESPACE
  可选: VOLCENGINE_REGION             (默认 cn-shanghai)
        VOLCENGINE_REGISTRY           (默认 cr.volces.com)
        IMAGE_TAG                     (支持 auto/latest；CI 中也可传入固定版本)
        VOLCENGINE_VPC_ID, VOLCENGINE_SUBNET_ID, VOLCENGINE_SECURITY_GROUP_ID
"""

import json
import os
import re
import sys
import time
import subprocess
import urllib.request
import urllib.error
import warnings
warnings.filterwarnings("ignore")

import volcenginesdkvefaas
import volcenginesdkapig20221112
import volcenginesdkcr
import volcenginesdkcore
from volcenginesdkapig20221112.api import APIG20221112Api

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ENV_FILE = os.path.join(ROOT_DIR, ".env.deploy")


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def configure_utf8_stdio() -> None:
    """Force UTF-8 stdio on Windows so deploy logs do not depend on GBK shells."""
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleCP(65001)
            kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass


configure_utf8_stdio()


def load_env_file(path: str, *, preserve_existing: bool = False) -> None:
    """Load .env-style values as UTF-8.

    By default `.env.deploy` is the source of truth for deployment values.
    Operators can opt into preserving shell overrides via
    `DEPLOY_PRESERVE_SHELL_ENV=1`.
    """
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip().strip('"').strip("'")
            if not name:
                continue
            existing = os.environ.get(name)
            if preserve_existing and existing not in (None, ""):
                continue
            os.environ[name] = value


load_env_file(
    DEFAULT_ENV_FILE,
    preserve_existing=_is_truthy(os.environ.get("DEPLOY_PRESERVE_SHELL_ENV")),
)

# svc key → function name in VeFaaS console (须与控制台函数名一致)
# type="python" 表示 AI 引擎（独立 Dockerfile，不同构建参数）
# 注意：ai-engine 在这里是逻辑服务入口，真正部署目标会在 resolve_target_services()
# 中展开为 gv-ai-engine-cn，海外分支已移除，后续只保留中国区部署。
SERVICES = [
    {"svc": "user-service",   "name": "gv-user-service",   "port": 3001, "internet": True},
    {"svc": "game-service",   "name": "gv-game-service",   "port": 3002},
    # social-service 已合入 feed-service（节省函数配额）
    {"svc": "feed-service",   "name": "gv-feed-service",   "port": 3004},
    # type="python": 独立 Dockerfile; internet=True: 需要访问公网（LLM API）
    {"svc": "ai-engine", "name": "ai-engine-logical", "port": 8000, "type": "python", "internet": True},
]

AK                = os.environ.get("VOLCENGINE_ACCESS_KEY",         "")
SK                = os.environ.get("VOLCENGINE_SECRET_KEY",         "")
REGION            = os.environ.get("VOLCENGINE_REGION",             "cn-shanghai")
REGISTRY          = os.environ.get("VOLCENGINE_REGISTRY",           "gamevallies-repo-cn-shanghai.cr.volces.com")
NAMESPACE         = os.environ.get("VOLCENGINE_REGISTRY_NAMESPACE",  "")
IMAGE_TAG         = os.environ.get("IMAGE_TAG",                     "").strip()
DOCKER_BUILD_NO_CACHE = os.environ.get("DOCKER_BUILD_NO_CACHE",    "").strip().lower() in {"1", "true", "yes", "on"}
VCR_USERNAME      = os.environ.get("VOLCENGINE_REGISTRY_USERNAME",  "")
VCR_PASSWORD      = os.environ.get("VOLCENGINE_REGISTRY_PASSWORD",  "")
VPC_ID            = os.environ.get("VOLCENGINE_VPC_ID",             "")
SUBNET_ID         = os.environ.get("VOLCENGINE_SUBNET_ID",          "")
SECURITY_GROUP_ID = os.environ.get("VOLCENGINE_SECURITY_GROUP_ID",  "")
VOLCENGINE_VPC_ID_CN_SHANGHAI = os.environ.get("VOLCENGINE_VPC_ID_CN_SHANGHAI", "").strip()
VOLCENGINE_SUBNET_ID_CN_SHANGHAI = os.environ.get("VOLCENGINE_SUBNET_ID_CN_SHANGHAI", "").strip()
VOLCENGINE_SECURITY_GROUP_ID_CN_SHANGHAI = os.environ.get("VOLCENGINE_SECURITY_GROUP_ID_CN_SHANGHAI", "").strip()
AI_ENGINE_URL     = os.environ.get("AI_ENGINE_URL",                 "")
AI_ENGINE_URL_CN_SHANGHAI = os.environ.get("AI_ENGINE_URL_CN_SHANGHAI", "").strip()
AI_ENGINE_DEFAULT_REGION = os.environ.get("AI_ENGINE_DEFAULT_REGION", os.environ.get("SERVICE_REGION", "cn_shanghai")).strip()
USER_SERVICE_URL  = os.environ.get("USER_SERVICE_URL",              "")
GAME_SERVICE_URL  = os.environ.get("GAME_SERVICE_URL",              "")
FEED_SERVICE_URL  = os.environ.get("FEED_SERVICE_URL",              "")
PUBLIC_API_BASE_URL = os.environ.get("PUBLIC_API_BASE_URL",         "")
GAME_SERVICE_UPSTREAM_URL = os.environ.get("GAME_SERVICE_UPSTREAM_URL", GAME_SERVICE_URL)
FEED_SERVICE_UPSTREAM_URL = os.environ.get("FEED_SERVICE_UPSTREAM_URL", FEED_SERVICE_URL)
VOLCENGINE_REGION_CN_SHANGHAI = os.environ.get("VOLCENGINE_REGION_CN_SHANGHAI", "cn-shanghai").strip()
VOLCENGINE_REGISTRY_CN_SHANGHAI = os.environ.get("VOLCENGINE_REGISTRY_CN_SHANGHAI", REGISTRY).strip()
AI_ENGINE_FUNCTION_NAME_CN_SHANGHAI = os.environ.get("AI_ENGINE_FUNCTION_NAME_CN_SHANGHAI", "gv-ai-engine-cn").strip()
APIG_PATCH_SAFE_METHOD = "PATCH"
DEFAULT_FUNCTION_REQUEST_TIMEOUT_S = 180
AI_ENGINE_TIMEOUT_HEADROOM_S = 60
APIG_TIMEOUT_MS_PER_SECOND = 1000
DEPLOY_PREFER_REMOTE_ENV = _is_truthy(os.environ.get("DEPLOY_PREFER_REMOTE_ENV"))

COMMON_DEPLOY_ENV_KEYS = [
    "VOLCENGINE_ACCESS_KEY",
    "VOLCENGINE_SECRET_KEY",
    "VOLCENGINE_REGISTRY_NAMESPACE",
    "VOLCENGINE_REGISTRY",
    "VOLCENGINE_REGISTRY_USERNAME",
    "VOLCENGINE_REGISTRY_PASSWORD",
]

COMMON_RUNTIME_ENV_KEYS = [
    "DATABASE_URL",
    "REDIS_URL",
    "JWT_SECRET",
    "JWT_REFRESH_SECRET",
    "PUBLIC_API_BASE_URL",
    "USER_SERVICE_URL",
    "GAME_SERVICE_URL",
    "FEED_SERVICE_URL",
]

USER_SERVICE_OPTIONAL_ENV_KEYS = [
    "ALIYUN_ACCESS_KEY_ID", "ALIYUN_ACCESS_KEY_SECRET",
    "ALIYUN_SMS_REGION_ID", "ALIYUN_SMS_SIGN_NAME",
    "ALIYUN_SMS_TPL_REGISTER", "ALIYUN_SMS_TPL_LOGIN",
    "VERIFY_CODE_SEND_INTERVAL_SECONDS",
    "WECHAT_MINIAPP_APP_ID", "WECHAT_MINIAPP_APP_SECRET",
    "WECHAT_H5_APP_ID", "WECHAT_H5_APP_SECRET", "WECHAT_H5_OAUTH_SCOPE",
    "WECHAT_PAY_MODE", "WECHAT_PAY_MERCHANT_ID",
    "WECHAT_PAY_NOTIFY_URL", "WECHAT_PAY_SERIAL_NO",
    "WECHAT_PAY_PRIVATE_KEY", "WECHAT_PAY_PRIVATE_KEY_PATH",
    "WECHAT_PAY_PUBLIC_KEY", "WECHAT_PAY_PUBLIC_KEY_PATH",
    "WECHAT_PAY_API_V3_KEY", "WECHAT_PAY_API_BASE",
    "ALIPAY_MODE", "ALIPAY_APP_ID",
    "ALIPAY_PRIVATE_KEY", "ALIPAY_PRIVATE_KEY_PATH",
    "ALIPAY_PUBLIC_KEY", "ALIPAY_PUBLIC_KEY_PATH",
    "ALIPAY_NOTIFY_URL", "ALIPAY_GATEWAY",
    "ALIPAY_SIGN_TYPE", "ALIPAY_SELLER_ID",
    "BILLING_DEFAULT_FREE_QUOTA",
]

USER_SERVICE_MANAGED_ENV_KEYS = set(COMMON_RUNTIME_ENV_KEYS + [
    "NODE_ENV",
    "PORT",
    "CORS_ORIGIN",
    "ADMIN_TOKEN",
    "AI_ENGINE_URL_CN_SHANGHAI",
    "AI_ENGINE_DEFAULT_REGION",
    "SERVICE_REGION",
    "AI_ENGINE_URL",
    "GAME_SERVICE_UPSTREAM_URL",
    "FEED_SERVICE_UPSTREAM_URL",
] + USER_SERVICE_OPTIONAL_ENV_KEYS)

AI_ENGINE_MANAGED_ENV_KEYS = {
    "ENVIRONMENT",
    "PORT",
    "DATABASE_URL",
    "REDIS_URL",
    "LLM_MODE",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_FAST_MODEL",
    "CORS_ORIGINS",
    "TEMPLATE_CONFIDENCE_THRESHOLD",
    "HYBRID_CONFIDENCE_THRESHOLD",
    "QA_MAX_RETRIES",
    "PIPELINE_TIMEOUT_S",
    "SERVICE_REGION",
    "LLM_GATEWAY_CACHE_TTL_S",
    "GAME_SERVICE_UPSTREAM_URL",
    "ADMIN_TOKEN",
}

GAME_SERVICE_MANAGED_ENV_KEYS = set(COMMON_RUNTIME_ENV_KEYS + [
    "NODE_ENV",
    "PORT",
    "CORS_ORIGIN",
    "ADMIN_TOKEN",
    "AI_ENGINE_URL_CN_SHANGHAI",
    "AI_ENGINE_DEFAULT_REGION",
    "SERVICE_REGION",
    "AI_ENGINE_URL",
    "APP_URL",
])

FEED_SERVICE_MANAGED_ENV_KEYS = set(COMMON_RUNTIME_ENV_KEYS + [
    "NODE_ENV",
    "PORT",
    "CORS_ORIGIN",
    "ADMIN_TOKEN",
    "AI_ENGINE_URL_CN_SHANGHAI",
    "AI_ENGINE_DEFAULT_REGION",
    "SERVICE_REGION",
    "AI_ENGINE_URL",
    "APP_URL",
])

REMOTE_FALLBACK_RUNTIME_ENV_KEYS = set().union(
    USER_SERVICE_MANAGED_ENV_KEYS,
    GAME_SERVICE_MANAGED_ENV_KEYS,
    FEED_SERVICE_MANAGED_ENV_KEYS,
    AI_ENGINE_MANAGED_ENV_KEYS,
)

SERVICE_REQUIRED_ENV_KEYS = {
    "user-service": [
        "GAME_SERVICE_UPSTREAM_URL",
        "FEED_SERVICE_UPSTREAM_URL",
        "WECHAT_PAY_MODE",
        "ALIPAY_MODE",
        "BILLING_DEFAULT_FREE_QUOTA",
        "WECHAT_MINIAPP_APP_ID",
        "WECHAT_MINIAPP_APP_SECRET",
        "ALIYUN_ACCESS_KEY_ID",
        "ALIYUN_ACCESS_KEY_SECRET",
        "ALIYUN_SMS_REGION_ID",
        "ALIYUN_SMS_SIGN_NAME",
        "ALIYUN_SMS_TPL_REGISTER",
        "ALIYUN_SMS_TPL_LOGIN",
        "VERIFY_CODE_SEND_INTERVAL_SECONDS",
    ],
    "game-service": [
    ],
    "feed-service": [],
    "ai-engine": [
        "LLM_MODE",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_FAST_MODEL",
        "GAME_SERVICE_UPSTREAM_URL",
    ],
}


def get_api(region_override: str | None = None) -> volcenginesdkvefaas.VEFAASApi:
    cfg = volcenginesdkcore.Configuration()
    cfg.ak = AK
    cfg.sk = SK
    cfg.region = region_override or REGION
    return volcenginesdkvefaas.VEFAASApi(volcenginesdkcore.ApiClient(cfg))


def get_apig_api(region_override: str | None = None) -> APIG20221112Api:
    cfg = volcenginesdkcore.Configuration()
    cfg.ak = AK
    cfg.sk = SK
    cfg.region = region_override or REGION
    return APIG20221112Api(volcenginesdkcore.ApiClient(cfg))


def get_cr_api(region_override: str | None = None) -> volcenginesdkcr.CRApi:
    cfg = volcenginesdkcore.Configuration()
    cfg.ak = AK
    cfg.sk = SK
    cfg.region = region_override or REGION
    return volcenginesdkcr.CRApi(volcenginesdkcore.ApiClient(cfg))


def _registry_instance_name(registry_host: str) -> str:
    explicit = os.environ.get("VOLCENGINE_REGISTRY_INSTANCE", "").strip()
    if explicit:
        return explicit

    host = (registry_host or "").strip().split(".", 1)[0]
    for suffix in (
        "-cn-shanghai",
        "-ap-southeast-1",
    ):
        if host.endswith(suffix):
            return host[:-len(suffix)]
    return host


def _list_repository_tags(
    api: volcenginesdkcr.CRApi,
    *,
    registry_instance: str,
    namespace: str,
    repository: str,
) -> list[str]:
    tags: list[str] = []
    page_number = 1
    page_size = 100

    while True:
        resp = api.list_tags(
            volcenginesdkcr.ListTagsRequest(
                registry=registry_instance,
                namespace=namespace,
                repository=repository,
                page_number=page_number,
                page_size=page_size,
            )
        )
        items = getattr(resp, "items", None) or []
        tags.extend(
            item.name
            for item in items
            if getattr(item, "name", None)
        )
        total_count = int(getattr(resp, "total_count", 0) or 0)
        if page_number * page_size >= total_count or not items:
            break
        page_number += 1

    return tags


def resolve_image_tag(target_services: list[dict]) -> str:
    requested = (IMAGE_TAG or "").strip()
    normalized_requested = requested.lower()
    auto_aliases = {"", "latest", "auto", "vn"}

    if requested and normalized_requested not in auto_aliases:
        return requested

    if normalized_requested in {"latest", "auto", "vn"}:
        print(f"⚠️  检测到 IMAGE_TAG={requested}，已自动切换为递增版本号标签")

    registry_instance = _registry_instance_name(REGISTRY)
    api = get_cr_api()
    max_version = 0

    for svc in target_services:
        repo = svc["name"]
        try:
            repo_tags = _list_repository_tags(
                api,
                registry_instance=registry_instance,
                namespace=NAMESPACE,
                repository=repo,
            )
        except Exception as exc:
            print(f"⚠️  读取 {repo} 现有标签失败，跳过版本扫描: {exc}")
            continue

        for tag in repo_tags:
            match = re.match(r"^v(\d+)(?:$|-)", tag)
            if match:
                max_version = max(max_version, int(match.group(1)))

    next_tag = f"v{max_version + 1}"
    print(f"🔖 自动分配镜像版本标签: {next_tag}")
    return next_tag


def get_function_id(api: volcenginesdkvefaas.VEFAASApi, name: str) -> str:
    resp = api.list_functions(volcenginesdkvefaas.ListFunctionsRequest(page_size=100))
    if resp.items:
        for f in resp.items:
            if f.name == name:
                return f.id
    return ""


def _safe_int(value: str | None, default: int) -> int:
    try:
        return int((value or "").strip() or default)
    except (TypeError, ValueError):
        return default


def _desired_request_timeout(svc: dict) -> int | None:
    if svc["svc"] not in {"ai-engine", "user-service", "game-service"}:
        return None
    pipeline_timeout = _safe_int(os.environ.get("PIPELINE_TIMEOUT_S"), 600)
    return max(DEFAULT_FUNCTION_REQUEST_TIMEOUT_S, pipeline_timeout + AI_ENGINE_TIMEOUT_HEADROOM_S)


def _desired_route_timeout(svc: dict) -> int | None:
    desired_request_timeout = _desired_request_timeout(svc)
    if desired_request_timeout is None:
        return None
    return desired_request_timeout * APIG_TIMEOUT_MS_PER_SECOND


def _service_env_prefix(svc: dict) -> str:
    return svc["svc"].upper().replace("-", "_")


def _optional_non_negative_int(env_key: str) -> int | None:
    raw = (os.environ.get(env_key) or "").strip()
    if not raw:
        return None
    value = _safe_int(raw, -1)
    if value < 0:
        raise ValueError(f"{env_key} must be a non-negative integer")
    return value


def _desired_function_resource_config(svc: dict) -> dict[str, int]:
    prefix = _service_env_prefix(svc)
    desired: dict[str, int] = {}
    for field, suffix in (
        ("min_instance", "MIN_INSTANCE"),
        ("max_instance", "MAX_INSTANCE"),
        ("reserved_frozen_instance", "RESERVED_FROZEN_INSTANCE"),
    ):
        value = _optional_non_negative_int(f"VEFAAS_{prefix}_{suffix}")
        if value is not None:
            desired[field] = value
    return desired


def _normalize_execution_region(value: str | None) -> str:
    return "cn_shanghai"


def _default_ai_engine_url_for_region(execution_region: str) -> str:
    if execution_region != "cn_shanghai":
        return ""
    return AI_ENGINE_URL_CN_SHANGHAI or AI_ENGINE_URL


def build_ai_engine_targets() -> list[dict]:
    return [
        {
            "svc": "ai-engine",
            "name": AI_ENGINE_FUNCTION_NAME_CN_SHANGHAI or "gv-ai-engine-cn",
            "port": 8000,
            "type": "python",
            "internet": True,
            "cloud_region": VOLCENGINE_REGION_CN_SHANGHAI or "cn-shanghai",
            "execution_region": "cn_shanghai",
            "registry": VOLCENGINE_REGISTRY_CN_SHANGHAI or REGISTRY,
        },
    ]


def resolve_target_services(target: str) -> list[dict]:
    if target == "ai-engine-cn":
        return [build_ai_engine_targets()[0]]

    selected = SERVICES if target == "all" else [s for s in SERVICES if s["svc"] == target]
    resolved: list[dict] = []

    for svc in selected:
        if svc["svc"] == "ai-engine":
            resolved.extend(build_ai_engine_targets())
        else:
            resolved.append(dict(svc))

    return resolved


def _get_admin_api_base_url() -> str:
    candidates = [
        (PUBLIC_API_BASE_URL or "").strip(),
        (GAME_SERVICE_URL or "").strip(),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        normalized = candidate.rstrip("/")
        if normalized.endswith("/api/v1"):
            return normalized
        return f"{normalized}/api/v1"
    return ""


def sync_ai_engine_region_target_deploy_state(svc: dict) -> bool:
    if svc["svc"] != "ai-engine":
        return True

    admin_base_url = _get_admin_api_base_url()
    if not admin_base_url:
        print("  ⚠️  跳过 Region Target 状态回写：未配置 PUBLIC_API_BASE_URL 或 GAME_SERVICE_URL")
        return True

    resolved_ai_engine_url = _default_ai_engine_url_for_region(svc.get("execution_region"))
    deploy_error = (svc.get("_deploy_meta") or {}).get("deploy_error")
    deploy_status = "failed" if deploy_error else "deployed"

    payload = {
        "executionRegion": svc.get("execution_region"),
        "deployStatus": deploy_status,
        "lastRevision": (svc.get("_deploy_meta") or {}).get("revision"),
        "lastImageTag": IMAGE_TAG,
        "lastReleaseStatus": (svc.get("_deploy_meta") or {}).get("release_status"),
        "lastDeployError": deploy_error,
    }
    if resolved_ai_engine_url:
        payload["aiEngineUrl"] = resolved_ai_engine_url
    elif deploy_status == "deployed":
        print(
            "  ⚠️  未提供可回写的 ai-engine URL，Region Target 将保留现有 endpoint；"
            "如需自动切流，请配置 AI_ENGINE_URL_CN_SHANGHAI"
        )

    request = urllib.request.Request(
        f"{admin_base_url}/admin/cloud/ai-engine-region-targets/sync-deploy",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-admin-token": os.environ.get("ADMIN_TOKEN", "admin123"),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8", errors="ignore")
            print(f"  ✅ Region Target 状态已回写: {svc.get('execution_region')} -> {response.status}")
            if body:
                print(f"     {body[:160]}")
        return True
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        print(f"  ⚠️  Region Target 状态回写失败: HTTP {exc.code} {detail[:200]}")
        return False
    except Exception as exc:
        print(f"  ⚠️  Region Target 状态回写失败: {exc}")
        return False


def _match_prefix_root_route(route: dict, svc: dict) -> bool:
    match_rule = route.get("match_rule") or {}
    path = match_rule.get("path") or {}
    return (
        route.get("name") == svc["name"]
        and path.get("match_type") == "Prefix"
        and path.get("match_content") == "/"
    )


def _build_match_rule_for_update(route: dict):
    match_rule = route.get("match_rule") or {}
    methods = list(match_rule.get("method") or [])
    if APIG_PATCH_SAFE_METHOD not in methods:
        methods.append(APIG_PATCH_SAFE_METHOD)

    path = match_rule.get("path") or {}
    return volcenginesdkapig20221112.MatchRuleForUpdateRouteInput(
        header=match_rule.get("header"),
        method=methods,
        path=volcenginesdkapig20221112.PathForUpdateRouteInput(
            match_content=path.get("match_content"),
            match_type=path.get("match_type"),
        ),
        query_string=match_rule.get("query_string"),
    )


def _build_advanced_setting_for_update(route: dict, desired_timeout: int | None = None):
    advanced = route.get("advanced_setting")
    if not advanced and desired_timeout is None:
        return None

    advanced = advanced or {}

    cors = advanced.get("cors_policy_setting")
    retry = advanced.get("retry_policy_setting")
    timeout = advanced.get("timeout_setting")
    rewrite = advanced.get("url_rewrite_setting")

    return volcenginesdkapig20221112.AdvancedSettingForUpdateRouteInput(
        cors_policy_setting=(
            volcenginesdkapig20221112.CorsPolicySettingForUpdateRouteInput(
                enable=cors.get("enable"),
                allow_credentials=cors.get("allow_credentials"),
                allow_headers=cors.get("allow_headers"),
                allow_methods=cors.get("allow_methods"),
                allow_origins=cors.get("allow_origins"),
                expose_headers=cors.get("expose_headers"),
                max_age=cors.get("max_age"),
            )
            if cors is not None
            else None
        ),
        header_operations=advanced.get("header_operations"),
        mirror_policies=advanced.get("mirror_policies"),
        retry_policy_setting=(
            volcenginesdkapig20221112.RetryPolicySettingForUpdateRouteInput(
                attempts=retry.get("attempts"),
                enable=retry.get("enable"),
                http_codes=retry.get("http_codes"),
                retry_on=retry.get("retry_on"),
            )
            if retry is not None
            else None
        ),
        timeout_setting=(
            volcenginesdkapig20221112.TimeoutSettingForUpdateRouteInput(
                enable=True if desired_timeout is not None else timeout.get("enable"),
                timeout=desired_timeout if desired_timeout is not None else timeout.get("timeout"),
            )
            if timeout is not None or desired_timeout is not None
            else None
        ),
        url_rewrite_setting=(
            volcenginesdkapig20221112.URLRewriteSettingForUpdateRouteInput(
                enable=rewrite.get("enable"),
                url_rewrite=rewrite.get("url_rewrite"),
            )
            if rewrite is not None
            else None
        ),
    )


def _build_upstream_list_for_update(route: dict):
    upstreams = []
    for item in route.get("upstream_list") or []:
        upstreams.append(
            volcenginesdkapig20221112.UpstreamListForUpdateRouteInput(
                ai_provider_settings=item.get("ai_provider_settings"),
                upstream_id=item.get("upstream_id"),
                version=item.get("version"),
                weight=item.get("weight"),
            )
        )
    return upstreams


def ensure_apig_patch_method(
    api: volcenginesdkvefaas.VEFAASApi,
    apig_api: APIG20221112Api,
    func_id: str,
    svc: dict,
) -> bool:
    try:
        triggers = api.list_triggers(volcenginesdkvefaas.ListTriggersRequest(function_id=func_id))
    except Exception as e:
        print(f"  ⚠️  跳过 APIG 方法校验：读取触发器失败: {e}")
        return True

    apig_trigger = None
    for item in triggers.items or []:
        trigger = item.to_dict()
        if trigger.get("type") == "apig":
            apig_trigger = trigger
            break

    if not apig_trigger:
        return True

    detail = json.loads(apig_trigger.get("detailed_config") or "{}")
    gateway_id = detail.get("GatewayId")
    upstream_id = detail.get("UpstreamId")
    if not gateway_id or not upstream_id:
        print("  ⚠️  跳过 APIG 方法校验：触发器缺少 GatewayId/UpstreamId")
        return True

    try:
        routes = apig_api.list_routes(
            volcenginesdkapig20221112.ListRoutesRequest(
                gateway_id=gateway_id,
                upstream_id=upstream_id,
                page_number=1,
                page_size=100,
            )
        )
    except Exception as e:
        print(f"  ⚠️  跳过 APIG 方法校验：读取路由失败: {e}")
        return True

    target_route = None
    for item in routes.items or []:
        route = item.to_dict()
        if _match_prefix_root_route(route, svc):
            target_route = route
            break

    if not target_route:
        print("  ⚠️  跳过 APIG 方法校验：未找到主路由")
        return True

    methods = list((target_route.get("match_rule") or {}).get("method") or [])
    desired_timeout = _desired_route_timeout(svc)
    current_timeout = ((target_route.get("advanced_setting") or {}).get("timeout_setting") or {}).get("timeout")
    current_timeout_enabled = ((target_route.get("advanced_setting") or {}).get("timeout_setting") or {}).get("enable")

    needs_patch_method = APIG_PATCH_SAFE_METHOD not in methods
    needs_timeout_sync = (
        desired_timeout is not None
        and (current_timeout != desired_timeout or not current_timeout_enabled)
    )

    if not needs_patch_method and not needs_timeout_sync:
        if desired_timeout is not None:
            print(
                f"  ✅ APIG 主路由已包含 {APIG_PATCH_SAFE_METHOD}，超时={desired_timeout}ms"
            )
        else:
            print(f"  ✅ APIG 主路由已包含 {APIG_PATCH_SAFE_METHOD}")
        return True

    try:
        apig_api.update_route(
            volcenginesdkapig20221112.UpdateRouteRequest(
                id=target_route["id"],
                name=target_route["name"],
                enable=target_route.get("enable"),
                priority=target_route.get("priority"),
                match_rule=_build_match_rule_for_update(target_route),
                advanced_setting=_build_advanced_setting_for_update(target_route, desired_timeout=desired_timeout),
                fallback_setting=target_route.get("fallback_setting"),
                upstream_list=_build_upstream_list_for_update(target_route),
            )
        )
        action_parts = []
        if needs_patch_method:
            action_parts.append(f"补齐 {APIG_PATCH_SAFE_METHOD}")
        if needs_timeout_sync and desired_timeout is not None:
            action_parts.append(f"同步超时到 {desired_timeout}ms")
        print(f"  ✅ 已为 APIG 主路由{'、'.join(action_parts)}")
        return True
    except Exception as e:
        print(f"  ❌ APIG 主路由校正失败: {e}")
        return False


def ensure_function_request_timeout(
    api: volcenginesdkvefaas.VEFAASApi,
    func_id: str,
    svc: dict,
) -> bool:
    desired_timeout = _desired_request_timeout(svc)
    if desired_timeout is None:
        return True

    try:
        function = api.get_function(volcenginesdkvefaas.GetFunctionRequest(id=func_id))
    except Exception as e:
        print(f"  ⚠️  跳过函数超时校验：读取函数失败: {e}")
        return True

    current_timeout = getattr(function, "request_timeout", None)
    if current_timeout == desired_timeout:
        print(f"  ✅ 函数请求超时已是 {desired_timeout}s")
        return True

    try:
        api.update_function(
            volcenginesdkvefaas.UpdateFunctionRequest(
                id=func_id,
                request_timeout=desired_timeout,
            )
        )
        rel = api.release(volcenginesdkvefaas.ReleaseRequest(function_id=func_id, revision_number=0))
        print(f"  ✅ 已将函数请求超时同步到 {desired_timeout}s（版本号: {rel.new_revision_number}，状态: {rel.status}）")
        return True
    except Exception as e:
        print(f"  ❌ 函数请求超时同步失败: {e}")
        return False


def ensure_function_instance_limits(
    api: volcenginesdkvefaas.VEFAASApi,
    func_id: str,
    svc: dict,
) -> bool:
    try:
        desired = _desired_function_resource_config(svc)
    except ValueError as exc:
        print(f"  Failed to parse desired function instance limits: {exc}")
        return False

    if not desired:
        return True

    try:
        resource = api.get_function_resource(
            volcenginesdkvefaas.GetFunctionResourceRequest(function_id=func_id)
        )
    except Exception as e:
        print(f"  Skipping function instance limit sync because resource lookup failed: {e}")
        return True

    current = getattr(resource, "function_resource", None)
    current_values = {
        "min_instance": getattr(current, "min_instance", None),
        "max_instance": getattr(current, "max_instance", None),
        "reserved_frozen_instance": getattr(current, "reserved_frozen_instance", None),
    }
    next_values = {**current_values, **desired}

    min_instance = next_values.get("min_instance")
    max_instance = next_values.get("max_instance")
    if (
        min_instance is not None
        and max_instance is not None
        and min_instance > max_instance
    ):
        print(
            "  Invalid function instance limits: "
            f"min_instance ({min_instance}) cannot exceed max_instance ({max_instance})"
        )
        return False

    changed_fields = {
        key: value
        for key, value in desired.items()
        if current_values.get(key) != value
    }
    if not changed_fields:
        print(
            "  Function instance limits already match desired values: "
            f"min={current_values.get('min_instance')}, "
            f"max={current_values.get('max_instance')}, "
            f"reserved={current_values.get('reserved_frozen_instance')}"
        )
        return True

    try:
        api.update_function_resource(
            volcenginesdkvefaas.UpdateFunctionResourceRequest(
                function_id=func_id,
                min_instance=next_values.get("min_instance"),
                max_instance=next_values.get("max_instance"),
                reserved_frozen_instance=next_values.get("reserved_frozen_instance"),
            )
        )
        print(
            "  Updated function instance limits: "
            f"min={next_values.get('min_instance')}, "
            f"max={next_values.get('max_instance')}, "
            f"reserved={next_values.get('reserved_frozen_instance')}"
        )
        return True
    except Exception as e:
        print(f"  Failed to update function instance limits: {e}")
        return False


def _env_vars(port: int, svc: dict | None = None) -> dict:
    """NestJS 服务公共环境变量"""
    svc_name = svc["svc"] if svc else ""
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
    if USER_SERVICE_URL:
        env["USER_SERVICE_URL"] = USER_SERVICE_URL
    if GAME_SERVICE_URL:
        env["GAME_SERVICE_URL"] = GAME_SERVICE_URL
    if FEED_SERVICE_URL:
        env["FEED_SERVICE_URL"] = FEED_SERVICE_URL
    if PUBLIC_API_BASE_URL:
        env["PUBLIC_API_BASE_URL"] = PUBLIC_API_BASE_URL
    if AI_ENGINE_URL_CN_SHANGHAI:
        env["AI_ENGINE_URL_CN_SHANGHAI"] = AI_ENGINE_URL_CN_SHANGHAI
    if AI_ENGINE_DEFAULT_REGION:
        env["AI_ENGINE_DEFAULT_REGION"] = _normalize_execution_region(AI_ENGINE_DEFAULT_REGION)
    if svc and svc.get("execution_region"):
        env["SERVICE_REGION"] = svc["execution_region"]

    default_ai_engine_url = AI_ENGINE_URL or _default_ai_engine_url_for_region(
        _normalize_execution_region(AI_ENGINE_DEFAULT_REGION)
    )
    if default_ai_engine_url:
        env["AI_ENGINE_URL"] = default_ai_engine_url

    public_api_base = PUBLIC_API_BASE_URL or ""
    if svc_name == "game-service":
        env["APP_URL"] = public_api_base or GAME_SERVICE_URL or "http://localhost:3002"
        env["GENERATION_QUEUE_ENABLED"] = os.environ.get("GENERATION_QUEUE_ENABLED", "true")
        env["GENERATION_QUEUE_WORKER_CONCURRENCY"] = os.environ.get(
            "GENERATION_QUEUE_WORKER_CONCURRENCY",
            "6",
        )
    elif svc_name == "feed-service":
        env["APP_URL"] = public_api_base or USER_SERVICE_URL or GAME_SERVICE_URL or "https://playforge.app"
    elif svc_name == "user-service":
        if GAME_SERVICE_UPSTREAM_URL:
            env["GAME_SERVICE_UPSTREAM_URL"] = GAME_SERVICE_UPSTREAM_URL
        if FEED_SERVICE_UPSTREAM_URL:
            env["FEED_SERVICE_UPSTREAM_URL"] = FEED_SERVICE_UPSTREAM_URL

    # 阿里云短信 Dysmsapi（仅 user-service 需要）
    if svc_name == "user-service":
        for key in USER_SERVICE_OPTIONAL_ENV_KEYS:
            val = os.environ.get(key, "")
            if val:
                env[key] = val

    return env


def _ai_env_vars(port: int, svc: dict | None = None) -> dict:
    """AI 引擎（Python）环境变量"""
    execution_region = (svc or {}).get("execution_region") or _normalize_execution_region(
        os.environ.get("SERVICE_REGION") or REGION
    )
    env = {
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
        "SERVICE_REGION":                execution_region,
        "LLM_GATEWAY_CACHE_TTL_S":       os.environ.get("LLM_GATEWAY_CACHE_TTL_S", "10"),
    }
    if GAME_SERVICE_UPSTREAM_URL:
        env["GAME_SERVICE_UPSTREAM_URL"] = GAME_SERVICE_UPSTREAM_URL
    if os.environ.get("ADMIN_TOKEN", ""):
        env["ADMIN_TOKEN"] = os.environ.get("ADMIN_TOKEN", "admin123")
    return env


def _extract_existing_envs(function) -> dict[str, str]:
    existing: dict[str, str] = {}
    for item in getattr(function, "envs", None) or []:
        key = getattr(item, "key", None)
        value = getattr(item, "value", None)
        if key:
            existing[str(key)] = "" if value is None else str(value)
    return existing


def hydrate_remote_runtime_env(target_services: list[dict]) -> None:
    if not DEPLOY_PREFER_REMOTE_ENV:
        return

    print("ℹ️  DEPLOY_PREFER_REMOTE_ENV=1，优先使用线上函数当前运行时环境变量")
    adopted_from_remote: dict[str, str] = {}
    drift_warnings: list[str] = []

    for svc in target_services:
        api = get_api(svc.get("cloud_region"))
        try:
            func_id = get_function_id(api, svc["name"])
            if not func_id:
                print(f"  ↪ 跳过 {svc['name']}：线上函数不存在，继续使用本地部署环境")
                continue
            function = api.get_function(volcenginesdkvefaas.GetFunctionRequest(id=func_id))
        except Exception as exc:
            print(f"  ⚠️  读取 {svc['name']} 线上环境失败，继续使用本地部署环境: {exc}")
            continue

        existing_envs = _extract_existing_envs(function)
        adopted_count = 0
        for key in REMOTE_FALLBACK_RUNTIME_ENV_KEYS:
            remote_value = (existing_envs.get(key) or "").strip()
            if not remote_value:
                continue

            current_value = (os.environ.get(key) or "").strip()
            adopted_source = adopted_from_remote.get(key)
            if adopted_source and current_value and current_value != remote_value:
                drift_warnings.append(
                    f"{key}: {adopted_source} 与 {svc['name']} 的线上值不一致"
                )
                continue

            os.environ[key] = remote_value
            adopted_from_remote.setdefault(key, svc["name"])
            adopted_count += 1

        if adopted_count:
            print(f"  ✅ 已从 {svc['name']} 注入/覆盖 {adopted_count} 个运行时环境变量")

    if drift_warnings:
        print("  ⚠️  检测到线上函数之间存在环境变量漂移，保留首次读取到的值：")
        for warning in drift_warnings[:10]:
            print(f"     - {warning}")
        if len(drift_warnings) > 10:
            print(f"     - ... 其余 {len(drift_warnings) - 10} 条已省略")


def _managed_env_keys(*, ai: bool = False, svc: dict | None = None) -> set[str]:
    if ai:
        return set(AI_ENGINE_MANAGED_ENV_KEYS)

    svc_name = svc["svc"] if svc else ""
    if svc_name == "user-service":
        return set(USER_SERVICE_MANAGED_ENV_KEYS)
    if svc_name == "game-service":
        return set(GAME_SERVICE_MANAGED_ENV_KEYS)
    if svc_name == "feed-service":
        return set(FEED_SERVICE_MANAGED_ENV_KEYS)
    return set(COMMON_RUNTIME_ENV_KEYS)


def build_envs_update(
    port: int,
    ai: bool = False,
    svc: dict | None = None,
    existing: dict[str, str] | None = None,
) -> list:
    src = _ai_env_vars(port, svc=svc) if ai else _env_vars(port, svc=svc)
    merged = dict(existing or {})
    managed_keys = _managed_env_keys(ai=ai, svc=svc)
    for key in managed_keys:
        merged.pop(key, None)
    merged.update(src)
    return [
        volcenginesdkvefaas.EnvForUpdateFunctionInput(key=k, value=v)
        for k, v in merged.items()
    ]


def build_envs_create(port: int, ai: bool = False, svc: dict | None = None) -> list:
    src = _ai_env_vars(port, svc=svc) if ai else _env_vars(port, svc=svc)
    return [
        volcenginesdkvefaas.EnvForCreateFunctionInput(key=k, value=v)
        for k, v in src.items()
    ]


def image_uri(svc: dict) -> str:
    registry = svc.get("registry") or REGISTRY
    return f"{registry}/{NAMESPACE}/{svc['name']}:{IMAGE_TAG}"


def _network_config_for_service(svc: dict) -> tuple[str, str, str]:
    execution_region = (svc.get("execution_region") or "").strip()
    cloud_region = (svc.get("cloud_region") or REGION).strip()

    if svc["svc"] != "ai-engine":
        return VPC_ID, SUBNET_ID, SECURITY_GROUP_ID

    if execution_region == "cn_shanghai":
        return (
            VOLCENGINE_VPC_ID_CN_SHANGHAI or (VPC_ID if cloud_region == REGION else ""),
            VOLCENGINE_SUBNET_ID_CN_SHANGHAI or (SUBNET_ID if cloud_region == REGION else ""),
            VOLCENGINE_SECURITY_GROUP_ID_CN_SHANGHAI or (SECURITY_GROUP_ID if cloud_region == REGION else ""),
        )

    return VPC_ID, SUBNET_ID, SECURITY_GROUP_ID


def _missing_env(keys: list[str]) -> list[str]:
    return [key for key in keys if not os.environ.get(key, "").strip()]


def validate_env(target_services: list[dict]) -> None:
    missing = set(_missing_env(COMMON_DEPLOY_ENV_KEYS + COMMON_RUNTIME_ENV_KEYS))

    for svc in target_services:
        missing.update(_missing_env(SERVICE_REQUIRED_ENV_KEYS.get(svc["svc"], [])))
        try:
            desired_limits = _desired_function_resource_config(svc)
        except ValueError as exc:
            print(f"鉂?閮ㄧ讲鍓嶇幆澧冨彉閲忔牎楠屽け璐? {exc}")
            sys.exit(1)
        if (
            desired_limits.get("min_instance") is not None
            and desired_limits.get("max_instance") is not None
            and desired_limits["min_instance"] > desired_limits["max_instance"]
        ):
            print(
                "鉂?閮ㄧ讲鍓嶇幆澧冨彉閲忔牎楠屽け璐ワ細"
                f"VEFAAS_{_service_env_prefix(svc)}_MIN_INSTANCE "
                f"({desired_limits['min_instance']}) cannot exceed "
                f"VEFAAS_{_service_env_prefix(svc)}_MAX_INSTANCE "
                f"({desired_limits['max_instance']})"
            )
            sys.exit(1)
        if svc["svc"] == "user-service":
            wechat_pay_mode = os.environ.get("WECHAT_PAY_MODE", "mock").strip().lower()
            if wechat_pay_mode == "real":
                missing.update(_missing_env([
                    "WECHAT_PAY_MERCHANT_ID",
                    "WECHAT_PAY_SERIAL_NO",
                    "WECHAT_PAY_API_V3_KEY",
                ]))

                has_private_key = bool(
                    os.environ.get("WECHAT_PAY_PRIVATE_KEY", "").strip()
                    or os.environ.get("WECHAT_PAY_PRIVATE_KEY_PATH", "").strip()
                )
                has_public_key = bool(
                    os.environ.get("WECHAT_PAY_PUBLIC_KEY", "").strip()
                    or os.environ.get("WECHAT_PAY_PUBLIC_KEY_PATH", "").strip()
                )
                if not has_private_key:
                    missing.add("WECHAT_PAY_PRIVATE_KEY or WECHAT_PAY_PRIVATE_KEY_PATH")
                if not has_public_key:
                    missing.add("WECHAT_PAY_PUBLIC_KEY or WECHAT_PAY_PUBLIC_KEY_PATH")

            alipay_mode = os.environ.get("ALIPAY_MODE", "").strip().lower()
            if alipay_mode == "real":
                missing.update(_missing_env([
                    "ALIPAY_APP_ID",
                    "ALIPAY_NOTIFY_URL",
                ]))

                has_alipay_private_key = bool(
                    os.environ.get("ALIPAY_PRIVATE_KEY", "").strip()
                    or os.environ.get("ALIPAY_PRIVATE_KEY_PATH", "").strip()
                )
                has_alipay_public_key = bool(
                    os.environ.get("ALIPAY_PUBLIC_KEY", "").strip()
                    or os.environ.get("ALIPAY_PUBLIC_KEY_PATH", "").strip()
                )
                if not has_alipay_private_key:
                    missing.add("ALIPAY_PRIVATE_KEY or ALIPAY_PRIVATE_KEY_PATH")
                if not has_alipay_public_key:
                    missing.add("ALIPAY_PUBLIC_KEY or ALIPAY_PUBLIC_KEY_PATH")

    if missing:
        print("❌ 部署前环境变量校验失败，缺少以下字段：")
        for key in sorted(missing):
            print(f"   - {key}")
        print("   请先补齐 .env.deploy（或当前 shell 环境）后再重试。")
        sys.exit(1)


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
    vpc_id, subnet_id, security_group_id = _network_config_for_service(svc)
    if not (vpc_id and subnet_id and security_group_id):
        return None
    return volcenginesdkvefaas.VpcConfigForCreateFunctionInput(
        enable_vpc=True,
        vpc_id=vpc_id,
        subnet_ids=[subnet_id],
        security_group_ids=[security_group_id],
        enable_shared_internet_access=svc.get("internet", False),
    )


def _vpc_config_update(svc: dict):
    """构造 VPC 配置（UpdateFunction 用）"""
    vpc_id, subnet_id, security_group_id = _network_config_for_service(svc)
    if not (vpc_id and subnet_id and security_group_id):
        return None
    return volcenginesdkvefaas.VpcConfigForUpdateFunctionInput(
        enable_vpc=True,
        vpc_id=vpc_id,
        subnet_ids=[subnet_id],
        security_group_ids=[security_group_id],
        enable_shared_internet_access=svc.get("internet", False),
    )


def create_function(api: volcenginesdkvefaas.VEFAASApi, svc: dict, image: str) -> str:
    """创建新函数，返回函数 ID。失败返回空字符串。"""
    name = svc["name"]
    is_ai = svc.get("type") == "python"
    request_timeout = _desired_request_timeout(svc)
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
        envs=build_envs_create(svc["port"], ai=is_ai, svc=svc),
        request_timeout=request_timeout,
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


def _build_update_function_request(
    func_id: str,
    svc: dict,
    image: str,
    existing_envs: dict[str, str],
    *,
    include_source_access_config: bool,
):
    is_ai = svc.get("type") == "python"
    request_timeout = _desired_request_timeout(svc)
    update_req = volcenginesdkvefaas.UpdateFunctionRequest(
        id=func_id,
        source_type="image",
        source=image,
        command=_svc_command(svc),
        envs=build_envs_update(svc["port"], ai=is_ai, svc=svc, existing=existing_envs),
        request_timeout=request_timeout,
    )
    if include_source_access_config:
        update_req.source_access_config = volcenginesdkvefaas.SourceAccessConfigForUpdateFunctionInput(
            username=VCR_USERNAME or AK,
            password=VCR_PASSWORD or SK,
        )
    vpc = _vpc_config_update(svc)
    if vpc:
        update_req.vpc_config = vpc
    return update_req


def deploy_service(api: volcenginesdkvefaas.VEFAASApi, svc: dict) -> bool:
    name   = svc["name"]
    image  = image_uri(svc)
    is_ai  = svc.get("type") == "python"
    request_timeout = _desired_request_timeout(svc)

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
    if DOCKER_BUILD_NO_CACHE:
        build_cmd.insert(2, "--no-cache")
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
        existing_envs: dict[str, str] = {}
        try:
            function_detail = api.get_function(volcenginesdkvefaas.GetFunctionRequest(id=func_id))
            existing_envs = _extract_existing_envs(function_detail)
        except Exception as e:
            print(f"  ⚠️  读取现有环境变量失败，将仅使用本次部署环境: {e}")
        # 4a. 更新已有函数
        update_req = _build_update_function_request(
            func_id,
            svc,
            image,
            existing_envs,
            include_source_access_config=True,
        )
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
        if func_id and VCR_USERNAME and VCR_PASSWORD:
            print(f"  🔁 镜像同步失败，尝试保留函数现有拉镜像凭据后重试...")
            try:
                retry_req = _build_update_function_request(
                    func_id,
                    svc,
                    image,
                    existing_envs if func_id else {},
                    include_source_access_config=False,
                )
                api.update_function(retry_req)
                print(f"  ✅ 已使用现有拉镜像凭据重新提交草稿")
            except Exception as e:
                print(f"  ❌ 保留现有拉镜像凭据重试失败: {e}")
                return False
            if not wait_image_sync(api, func_id, image):
                return False
        else:
            return False

    if not ensure_function_instance_limits(api, func_id, svc):
        return False

    # 6. 发布新版本（revision_number=0 = 当前最新草稿）
    print(f"  🚀 发布新版本...")
    try:
        rel = api.release(volcenginesdkvefaas.ReleaseRequest(function_id=func_id, revision_number=0))
        svc["_deploy_meta"] = {
            "revision": str(getattr(rel, "new_revision_number", "") or ""),
            "release_status": getattr(rel, "status", "") or "",
            "deploy_error": None,
        }
        print(f"  ✅ 发布成功（版本号: {rel.new_revision_number}，状态: {rel.status}）")
    except Exception as e:
        svc["_deploy_meta"] = {
            "revision": None,
            "release_status": "failed",
            "deploy_error": str(e),
        }
        print(f"  ❌ 发布失败: {e}")
        return False

    return True


def main():
    global IMAGE_TAG

    if not AK or not SK:
        print("❌ 请设置 VOLCENGINE_ACCESS_KEY 和 VOLCENGINE_SECRET_KEY")
        sys.exit(1)
    if not NAMESPACE:
        print("❌ 请设置 VOLCENGINE_REGISTRY_NAMESPACE（镜像仓库命名空间）")
        print("   控制台 → 容器镜像服务 → 命名空间 → 创建后填入此变量")
        sys.exit(1)

    requested_targets = sys.argv[1:] if len(sys.argv) > 1 else ["all"]
    services: list[dict] = []
    seen_service_names: set[str] = set()
    for target in requested_targets:
        resolved = resolve_target_services(target)
        if not resolved:
            print(f"❌ 未知服务: {target}，可选: {[s['svc'] for s in SERVICES]} | ai-engine-cn | all")
            sys.exit(1)
        for svc in resolved:
            if svc["name"] in seen_service_names:
                continue
            seen_service_names.add(svc["name"])
            services.append(svc)

    if not services:
        print(f"❌ 未匹配到任何服务，可选: {[s['svc'] for s in SERVICES]} | ai-engine-cn | all")
        sys.exit(1)

    hydrate_remote_runtime_env(services)
    validate_env(services)
    IMAGE_TAG = resolve_image_tag(services)

    print(f"📍 部署配置:")
    print(f"   地域:             {REGION}")
    print(f"   镜像仓库:         {REGISTRY}/{NAMESPACE}")
    print(f"   镜像 Tag:         {IMAGE_TAG}")
    print(f"   VPC ID:           {VPC_ID or '未设置'}")
    print(f"   Subnet ID:        {SUBNET_ID or '未设置'}")
    print(f"   Security Group:   {SECURITY_GROUP_ID or '未设置（跳过 VPC 配置）'}")
    print(f"   上海 VPC:         {VOLCENGINE_VPC_ID_CN_SHANGHAI or VPC_ID or '未设置'}")
    print(f"   统一公网域名:     {PUBLIC_API_BASE_URL or '未设置（使用各服务默认域名）'}")
    print(f"   AI 默认执行 Region: {_normalize_execution_region(AI_ENGINE_DEFAULT_REGION)}")
    print(f"   AI 上海函数:      {AI_ENGINE_FUNCTION_NAME_CN_SHANGHAI} @ {VOLCENGINE_REGION_CN_SHANGHAI}")

    print(f"\n🚀 开始部署 {len(services)} 个服务...")
    failed = []
    for svc in services:
        api = get_api(svc.get("cloud_region"))
        apig_api = get_apig_api(svc.get("cloud_region"))
        print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"🔨 {svc['name']}")
        if svc.get("cloud_region"):
            print(f"  🌍 云地域: {svc['cloud_region']} -> 执行 Region: {svc.get('execution_region', '-')}")
        if not deploy_service(api, svc):
            failed.append(svc["name"])
            continue
        func_id = get_function_id(api, svc["name"])
        if not func_id:
            print(f"  ❌ 无法读取 {svc['name']} 的函数 ID，跳过 APIG 方法校验")
            failed.append(svc["name"])
            continue
        if not ensure_function_request_timeout(api, func_id, svc):
            failed.append(svc["name"])
            continue
        if not ensure_apig_patch_method(api, apig_api, func_id, svc):
            failed.append(svc["name"])
            continue
        if not sync_ai_engine_region_target_deploy_state(svc):
            failed.append(svc["name"])
            continue

    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if failed:
        print(f"❌ 部署失败: {failed}")
        sys.exit(1)
    print("🎉 所有服务部署完成！")


if __name__ == "__main__":
    main()
