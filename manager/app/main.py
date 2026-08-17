"""AI 网关管理服务（control plane）

职责：把散落的账号（Codex / OpenCode Zen·Go / Command Code）在你的网关前面板里
统一管理 —— 自动初始化 new-api、创建渠道与统一令牌、把 Command Code 密钥写进
cc-adapter、查询各渠道余额/用量。本身不存业务数据，凭据保存在挂载卷 settings.json。

依赖的新-api 管理 API（均已按源码核实，见 ref/new-api）：
  GET  /api/setup                          # 初始化状态
  POST /api/setup                          # 首次创建 root 管理员
  POST /api/user/login                     # 登录 -> data.access_token
  GET  /api/channel/                       # 渠道列表
  POST /api/channel/                       # 创建渠道 {mode, channel:{...}}
  DELETE /api/channel/:id                  # 删除渠道
  GET  /api/channel/:id/codex/usage        # codex 渠道用量
  POST /api/token/                         # 创建令牌 {token:{...}}
  GET  /api/token/                         # 令牌列表
  POST /api/token/:id/key                  # 取令牌完整 key

cc-adapter 管理 API：
  POST /admin/api/login                    # {password} -> {token}
  PUT  /admin/api/config                   # {cc_api_key: "[k1,k2]"}
  POST /admin/api/usage/query              # 每个 CC key 的余额/套餐/窗口用量
  GET  /admin/api/models                   # 模型清单（免鉴权）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

log = logging.getLogger("manager")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
SETTINGS_PATH = DATA_DIR / "settings.json"
NEW_API_URL = os.environ.get("NEW_API_URL", "http://new-api:3000").rstrip("/")
CC_ADAPTER_URL = os.environ.get("CC_ADAPTER_URL", "http://cc-adapter:8080").rstrip("/")
ADAPTER_ADMIN_PASSWORD = os.environ.get("ADAPTER_ADMIN_PASSWORD", "")
ADAPTER_ACCESS_KEY = os.environ.get("ADAPTER_ACCESS_KEY", "")
# 额度同步触发方式：
#  - 事件驱动（默认开）：检测到 new-api 渠道被“测试”（test_time/response_time 变化）立即同步
#  - 周期兜底（默认关）：SYNC_INTERVAL_SECONDS > 0 时每 N 秒同步一次
TEST_POLL_INTERVAL = max(3, int(os.environ.get("TEST_POLL_INTERVAL_SECONDS", "10")))
PERIODIC_SYNC = int(os.environ.get("SYNC_INTERVAL_SECONDS", "0"))

ZEN_BASE = "https://opencode.ai/zen/v1"
# OpenCode Go 与 Zen 是两套独立端点。注意 new-api 会往渠道 base_url 后面拼
# /v1/chat/completions，所以渠道 base 不带 /v1；探测/查余额用带 /v1 的 probe_base。
OPENCODE_BASES = [
    ("https://opencode.ai/zen/go", "https://opencode.ai/zen/go/v1"),
    ("https://opencode.ai/zen", "https://opencode.ai/zen/v1"),
]
MANAGED_TAG = "gw-managed"
TOKEN_NAME = "gw-token"

# 自适应共享模型：同一模型名注册到多个渠道，new-api 按渠道优先级 + 失败重试自动切换。
# 优先级数字越大越先试（new-api 渠道编辑里的"优先级"字段，可随时在面板直接改顺序）。
ADAPTIVE_MODELS = ["gpt-5.6-luna"]
CHANNEL_PRIORITY = {"codex": 3, "opencode": 2, "cc": 1}  # 组内排序：Codex 优先
RETRY_TIMES = 3  # 重试次数 = 优先级层级数（codex→opencode→cc 共切 2 次，留 1 余量）
# 重试状态码：默认不含 400，但 codex 渠道对 chat 请求返回 400（endpoint not supported），
# 必须把 400 加入重试列表，chat 请求才会从 codex 自动切到 opencode/cc。
RETRY_STATUS_CODES = "100-199,300-399,400-407,409-499,500-503,505-523,525-599"
# 渠道归属分组（组=提供商；令牌 auto_groups 顺序 = 渠道尝试顺序）
CHANNEL_GROUPS = {"codex": "codex", "opencode": "opencode", "cc": "cc"}
# 需要注册到分组倍率配置的组（IsUserSelectableGroup 要求组有倍率）
REQUIRED_GROUPS = ["default", "vip", "svip", "codex", "opencode", "cc"]

CODEX_MODELS = [
    "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5",
    "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex-spark",
]
# OpenCode Go 官方 /zen/go/v1/models 返回的 26 个模型（2026 实测），作探测失败时的兜底
OC_FALLBACK_MODELS = [
    "minimax-m3", "minimax-m2.7", "minimax-m2.5", "kimi-k3", "kimi-k2.7-code",
    "kimi-k2.6", "kimi-k2.5", "glm-5.3", "glm-5.2", "glm-5.1", "glm-5",
    "deepseek-v4-pro", "deepseek-v4-flash", "qwen3.8-max", "qwen3.7-max",
    "qwen3.7-plus", "qwen3.6-plus", "qwen3.5-plus", "mimo-v2-pro", "mimo-v2-omni",
    "mimo-v2.5-pro", "mimo-v2.5", "hy3", "hy3-preview", "gpt-5.6-luna", "grok-4.5",
]
CC_FALLBACK_MODELS = [
    "claude-sonnet-4-6", "claude-opus-4-6", "claude-opus-5", "claude-haiku-4-5",
    "gpt-5.6-luna", "gpt-5.5", "gpt-5.4", "gpt-5.3-codex", "gpt-5.4-mini",
    "deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-flash", "qwen-3-7-max", "mimo-v2.5",
]

app = FastAPI(title="AI Gateway Manager")


# ---------------------------------------------------------------- 后台事件检测

_last_channel_state: dict[int, tuple[int, int]] = {}  # channel_id -> (test_time, response_time)


async def _background_loop() -> None:
    """每 TEST_POLL_INTERVAL 秒对比渠道状态：
    检测到 new-api 里渠道被“测试”（test_time/response_time 变化）→ 立即同步额度到备注；
    可选周期性兜底（PERIODIC_SYNC > 0）。"""
    periodic = PERIODIC_SYNC if PERIODIC_SYNC > 0 else None
    last_periodic = 0.0
    while True:
        try:
            await asyncio.sleep(TEST_POLL_INTERVAL)
            s = load_settings()
            if not s.get("newapi_username") or not s.get("newapi_password"):
                continue
            client = Client(NEW_API_URL)
            client.setup_and_login(s["newapi_username"], s["newapi_password"])
            channels = client.list_channels()

            triggered = False
            for ch in channels:
                if ch.get("tag") != MANAGED_TAG:
                    continue
                cid = int(ch["id"])
                state = (int(ch.get("test_time") or 0), int(ch.get("response_time") or 0))
                if cid in _last_channel_state and state != _last_channel_state[cid]:
                    out = sync_remarks(client, s)
                    log.info("channel #%s tested -> quota synced: %s",
                             cid, ", ".join(f"#{x['id']}={x['remark']}" for x in out))
                    triggered = True
                    break
            for ch in channels:
                if ch.get("tag") == MANAGED_TAG:
                    cid = int(ch["id"])
                    _last_channel_state[cid] = (int(ch.get("test_time") or 0), int(ch.get("response_time") or 0))

            if not triggered and periodic and time.time() - last_periodic > periodic:
                last_periodic = time.time()
                out = sync_remarks(client, s)
                if out:
                    log.info("periodic quota sync: %d remark(s)", len(out))
        except Exception as e:  # 后台任务永不退出
            log.warning("background loop error: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_background_loop())
    yield
    task.cancel()


app = FastAPI(title="AI Gateway Manager", lifespan=lifespan)


# ---------------------------------------------------------------- settings

def default_settings() -> dict:
    return {
        "newapi_url": NEW_API_URL,
        "newapi_username": "",
        "newapi_password": "",
        "codex_accounts": [],
        "opencode_keys": [],
        "cc_keys": [],
        "cc_access_key": ADAPTER_ACCESS_KEY,
        # 路由策略：按模型配置渠道顺序。每一条策略 = 一个 auto 分组令牌：
        # 模型列表(model_limits) + 渠道顺序(auto_groups)，失败自动跨组重试。
        # codex 注册了裸名 luna（responses 客户端可走 codex→opencode→cc 自适应）；
        # chat 客户端请用 luna-chat 策略（opencode→cc），避开官方 chat↔responses 流式转换缺陷。
        "policies": [
            {"name": "luna-adaptive", "models": "gpt-5.6-luna", "groups": "codex,opencode,cc"},
            {"name": "luna-chat", "models": "gpt-5.6-luna", "groups": "opencode,cc"},
        ],
    }


def _migrate_policies(policies: list) -> list:
    """策略配置迁移：旧默认(luna-adaptive=opencode,cc)升级为 codex,opencode,cc 并补齐 luna-chat。"""
    has_adaptive = any(p.get("name") == "luna-adaptive" for p in policies)
    has_chat = any(p.get("name") == "luna-chat" for p in policies)
    out = []
    for p in policies:
        if p.get("name") == "luna-adaptive" and str(p.get("groups", "")) == "opencode,cc":
            p = {"name": "luna-adaptive", "models": "gpt-5.6-luna", "groups": "codex,opencode,cc"}
        out.append({k: str(p.get(k, "")).strip() for k in ("name", "models", "groups")})
    if not has_adaptive:
        out.insert(0, {"name": "luna-adaptive", "models": "gpt-5.6-luna", "groups": "codex,opencode,cc"})
    if not has_chat:
        out.append({"name": "luna-chat", "models": "gpt-5.6-luna", "groups": "opencode,cc"})
    return [p for p in out if p["name"] and p["models"] and p["groups"]]


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                merged = default_settings()
                merged.update(data)
                merged["policies"] = _migrate_policies(merged.get("policies") or [])
                return merged
        except Exception:
            pass
    return default_settings()


def save_settings(s: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def mask_key(k: str) -> str:
    if not k:
        return ""
    return k[:4] + "****" if len(k) > 8 else "*" * len(k)


# ---------------------------------------------------------------- http helpers

def _extract(r: dict) -> Any:
    """new-api 响应一般为 {success, message, data}；这里宽松提取 data 或原值。"""
    if isinstance(r, dict):
        if "data" in r:
            return r["data"]
        if "success" in r and r.get("success") is False:
            raise RuntimeError(str(r.get("message", "请求失败")))
    return r


def _items(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("channels", "tokens", "items", "list"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


class Client:
    """对 new-api 管理 API 的封装。"""

    def __init__(self, base: str):
        self.base = base
        self.token = ""
        self.c = httpx.Client(base_url=base, timeout=15.0)

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def raw(self, method: str, path: str, **kw) -> dict:
        for attempt in range(4):
            try:
                resp = self.c.request(method, path, headers=self._h(), **kw)
            except httpx.RequestError as e:
                raise RuntimeError(f"无法连接 new-api ({self.base}): {e}")
            if resp.status_code == 429 and attempt < 3:  # 全局/关键接口限流，退避重试
                time.sleep(3 + attempt * 3)
                continue
            try:
                body = resp.json()
            except Exception:
                raise RuntimeError(f"new-api 返回非 JSON: {resp.status_code} {resp.text[:200]}")
            if isinstance(body, dict) and body.get("success") is False:
                raise RuntimeError(str(body.get("message", f"new-api 报错 {resp.status_code}")))
            return body
        raise RuntimeError("new-api 限流(429)重试后仍失败，请等十几秒再点一次")

    def setup_and_login(self, username: str, password: str) -> None:
        # 登录接口有 CriticalRateLimit，令牌有有效期，缓存起来避免频繁登录
        s = load_settings()
        cached = s.get("newapi_access_token", "")
        exp = float(s.get("newapi_token_expires", 0) or 0)
        if cached and exp > time.time() + 60:
            self.token = cached
            return

        setup = self.raw("GET", "/api/setup")["data"]
        if not setup.get("status"):
            if not setup.get("root_init"):
                self.raw("POST", "/api/setup", json={
                    "username": username, "password": password,
                    "confirmPassword": password,
                    "SelfUseModeEnabled": True, "DemoSiteEnabled": False,
                })
            else:
                self.raw("POST", "/api/setup", json={
                    "SelfUseModeEnabled": True, "DemoSiteEnabled": False,
                })
        body = self.raw("POST", "/api/user/login", json={
            "username": username, "password": password,
        })
        data = body.get("data", {})
        token = data.get("access_token", "")
        if not token:
            raise RuntimeError("登录 new-api 失败：未拿到 access_token")
        self.token = token
        expires = data.get("access_expires_at") or (time.time() + 600)
        s["newapi_access_token"] = token
        s["newapi_token_expires"] = float(expires)
        save_settings(s)

    def list_channels(self) -> list:
        out: list = []
        page = 1
        while True:
            data = _extract(self.raw("GET", "/api/channel/", params={"page": page, "page_size": 100}))
            items = _items(data)
            out.extend(items)
            total = data.get("total") if isinstance(data, dict) else None
            if total is None or len(out) >= int(total) or not items:
                break
            page += 1
        return out

    def add_channel(self, channel: dict, mode: str = "single") -> int:
        self.raw("POST", "/api/channel/", json={"mode": mode, "channel": channel})
        # 创建接口不回传 id，按名字回查（幂等：同一名字只取最新的一个）
        name = channel.get("name")
        for ch in self.list_channels():
            if ch.get("name") == name:
                return int(ch["id"])
        return -1

    def delete_channel(self, cid: int) -> None:
        self.raw("DELETE", f"/api/channel/{cid}")

    def codex_usage(self, cid: int) -> dict:
        return self.raw("GET", f"/api/channel/{cid}/codex/usage")

    def list_tokens(self) -> list:
        out: list = []
        page = 1
        while True:
            data = _extract(self.raw("GET", "/api/token/", params={"page": page, "page_size": 100}))
            items = _items(data)
            out.extend(items)
            total = data.get("total") if isinstance(data, dict) else None
            if total is None or len(out) >= int(total) or not items:
                break
            page += 1
        return out

    def add_token(self, name: str, extra: dict | None = None) -> int:
        body = {
            "name": name, "expired_time": -1, "remain_quota": 0,
            "unlimited_quota": True, "model_limits_enabled": False,
            "model_limits": "", "group": "default", "allow_ips": None,
        }
        if extra:
            body.update(extra)
        self.raw("POST", "/api/token/", json=body)
        found = [t for t in self.list_tokens() if t.get("name") == name]
        found.sort(key=lambda t: int(t.get("id", 0)))
        return int(found[-1]["id"]) if found else -1

    def delete_token(self, tid: int) -> None:
        self.raw("DELETE", f"/api/token/{tid}")

    def update_channel_remark(self, cid: int, remark: str) -> None:
        # PatchChannel 直接绑定扁平字段；remark 为非敏感可写字段
        self.raw("PUT", "/api/channel/", json={"id": cid, "remark": remark})

    def update_token(self, tid: int, fields: dict) -> None:
        # tokenRequest 扁平结构（内嵌 model.Token），key 保持不变
        self.raw("PUT", "/api/token/", json={"id": tid, **fields})

    def set_option(self, key: str, value: str) -> None:
        self.raw("PUT", "/api/option/", json={"key": key, "value": value})

    def get_option(self, key: str) -> str:
        body = self.raw("GET", "/api/option/")
        data = body.get("data")
        if isinstance(data, list):  # 选项接口返回 [{key, value}, ...]
            for item in data:
                if isinstance(item, dict) and item.get("key") == key:
                    return str(item.get("value", ""))
        elif isinstance(data, dict) and key in data:
            return str(data[key])
        return ""

    def ensure_groups(self) -> None:
        """注册分组倍率 + 用户可用分组（auto 分组令牌的两个前提条件）。

        IsUserSelectableGroup 要求：组在 UserUsableGroups 里 && 组在 GroupRatio 里。
        """
        desc = {"default": "默认分组", "vip": "vip分组", "svip": "svip分组",
                "auto": "自动分组", "codex": "Codex 渠道组",
                "opencode": "OpenCode 渠道组", "cc": "Command Code 渠道组"}
        for key, ratio in (("GroupRatio", True), ("UserUsableGroups", False)):
            current: dict = {}
            raw = self.get_option(key)
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        current = parsed
                except Exception:
                    pass
            groups = REQUIRED_GROUPS + (["auto"] if not ratio else [])
            for g in groups:
                if g not in current:
                    current[g] = 1 if ratio else desc.get(g, g)
            self.set_option(key, json.dumps(current))
        # 关闭渠道亲和规则（默认 "codex cli trace" 规则对 gpt/responses 请求
        # skip_retry_on_failure=true，会把 codex CLI 请求粘死在 codex 渠道，
        # 额度耗尽(429/401)时永远无法自动切到 opencode）。
        # 代价：codex 会话头透传随规则关闭（个人单账号影响很小）。
        self.set_option("channel_affinity_setting.rules", "[]")

    def ensure_token(self, name: str) -> tuple[int, str]:
        """清理历史上产生的无名垃圾令牌，保证只有一个 name 令牌（key 保持不变）。

        自适应顺序不靠分组，靠渠道"优先级"字段（默认 codex 3 > opencode 2 > cc 1，
        面板渠道编辑里可随时改），所以令牌保持 default 分组即可。
        """
        for t in self.list_tokens():
            if not t.get("name"):  # 早期版本接口字段用错产生的匿名令牌
                try:
                    self.delete_token(int(t["id"]))
                except Exception:
                    pass
        fields = {
            "name": name, "expired_time": -1, "remain_quota": 0,
            "unlimited_quota": True, "model_limits_enabled": False,
            "model_limits": "", "group": "auto", "cross_group_retry": True,
            "auto_groups": ["codex", "opencode", "cc", "default"],
        }
        mine = [t for t in self.list_tokens() if t.get("name") == name]
        mine.sort(key=lambda t: int(t.get("id", 0)))
        if mine:
            tid = int(mine[-1]["id"])
            self.update_token(tid, fields)
        else:
            tid = self.add_token(name, fields)
        return tid, self.token_key(tid)

    def token_key(self, tid: int) -> str:
        data = _extract(self.raw("POST", f"/api/token/{tid}/key"))
        return data.get("key", "") if isinstance(data, dict) else str(data)


class Adapter:
    """cc-adapter 管理 API 封装，凭据来自 compose 注入的 env（setup 时自动生成）。"""

    def __init__(self, base: str, admin_password: str):
        self.base = base
        self.admin_password = admin_password
        self.token = ""
        self.c = httpx.Client(base_url=base, timeout=15.0)

    def login(self) -> None:
        if not self.admin_password:
            raise RuntimeError("未配置 adapter 管理密码（CC_ADAPTER_ADMIN_PASSWORD）")
        body = self.c.post("/admin/api/login", json={"password": self.admin_password})
        body.raise_for_status()
        self.token = body.json().get("token", "")
        if not self.token:
            raise RuntimeError("adapter 登录失败")

    def _auth(self) -> dict:
        if not self.token:
            self.login()
        return {"Authorization": f"Bearer {self.token}"}

    def health(self) -> dict:
        try:
            r = self.c.get("/health")
            if r.status_code >= 500:
                return {"status": "down", "detail": r.text[:120]}
            result = {"status": "ok", "reachable": True}
            # /admin/api/health 需要管理token，先登录再补细节
            try:
                self.login()
                h = self.c.get("/admin/api/health", headers=self._auth())
                if h.status_code < 300:
                    detail = h.json()
                    result["cc_api_key_configured"] = detail.get("cc_api_key_configured")
                    result["version"] = detail.get("version")
            except Exception:
                pass
            return result
        except httpx.RequestError as e:
            return {"status": "down", "detail": str(e)}

    def models(self) -> list:
        try:
            r = self.c.get("/admin/api/models")
            return [m["id"] for m in r.json().get("models", [])]
        except Exception:
            return []

    def set_cc_keys(self, keys: list[str]) -> None:
        if not keys:
            raise RuntimeError("Command Code 密钥为空")
        body = self.c.put("/admin/api/config", headers=self._auth(),
                          json={"cc_api_key": json.dumps(keys)})
        if body.status_code >= 400:
            raise RuntimeError(f"adapter 配置更新失败: {body.status_code} {body.text[:200]}")

    def usage_query(self, keys: list[str]) -> list:
        if not keys:
            return []
        r = self.c.post("/admin/api/usage/query", headers=self._auth())
        if r.status_code >= 400:
            raise RuntimeError(f"adapter 用量查询失败: {r.status_code} {r.text[:200]}")
        return r.json() if isinstance(r.json(), list) else []


def ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 额度同步

def _query_opencode(key: str) -> tuple[str | None, dict | None]:
    """探测 OpenCode Go/Zen 端点并拉官方 /usage，返回 (probe_base, data)。"""
    for _ch, probe in OPENCODE_BASES:
        try:
            r = httpx.get(f"{probe}/usage", headers={"Authorization": f"Bearer {key}"}, timeout=15.0)
            if r.status_code == 200:
                return probe, r.json()
        except Exception:
            continue
    return None, None


def _oc_remark(base: str | None, data: dict | None) -> str:
    if not data:
        return "额度查询失败"
    u = data.get("usage", {}) if isinstance(data, dict) else {}
    parts = []
    for k, label in (("rolling", "5h"), ("weekly", "周"), ("monthly", "月")):
        v = u.get(k) or {}
        pct = v.get("percent")
        if pct is not None:
            parts.append(f"{label}{pct}%")
    plan = "Go" if base and "zen/go" in base else "Zen"
    return f"{plan}额度 " + (" ".join(parts) if parts else "?")


def _cc_remark(row: dict) -> str:
    cr = row.get("credits") or {}
    sub = row.get("subscription") or {}
    us = row.get("usage") or {}
    fh = us.get("fiveHour") or {}
    wk = us.get("weekly") or {}
    parts = []
    if cr.get("total") is not None:
        parts.append(f"总额度{cr['total']}")
    if sub.get("plan_name"):
        parts.append(sub["plan_name"])
    if fh.get("used") is not None:
        parts.append(f"5h {fh['used']}/{fh.get('cap') if fh.get('cap') else '∞'}")
    if wk.get("used") is not None:
        parts.append(f"周 {wk['used']}/{wk.get('cap') if wk.get('cap') else '∞'}")
    return "CC " + " ".join(parts) if parts else "CC ?"


def _codex_remark(data: dict) -> str:
    """codex 官方 usage 数据 -> 摘要（Plus 是限流制：看周窗口百分比/是否触限）。"""
    if not isinstance(data, dict):
        return "Codex ?"
    rl = data.get("rate_limit") or {}
    pw = rl.get("primary_window") or {}
    pct = pw.get("used_percent")
    plan = data.get("plan_type") or "?"
    parts = [f"Codex {plan}"]
    if pct is not None:
        parts.append(f"周窗{pct}%")
    if rl.get("allowed") is False or rl.get("limit_reached") is True:
        parts.append("已触限")
    cr = data.get("credits") or {}
    if cr.get("unlimited"):
        parts.append("无限额度")
    elif cr.get("has_credits"):
        parts.append(f"余额{cr.get('balance')}")
    return " ".join(parts)


def sync_remarks(client: Client, s: dict) -> list[dict]:
    """把 OpenCode/CC/Codex 的官方额度写进 new-api 渠道备注列。"""
    out: list[dict] = []
    remarks: dict[int, str] = {}
    for item in s["opencode_keys"]:
        key = item.get("key", "").strip()
        if not key:
            continue
        base, data = _query_opencode(key)
        if not base:
            continue
        label = item.get("label", "")
        name = f"opencode-{label}" if label else ""
        if not name:
            continue
        for ch in client.list_channels():
            if ch.get("name") == name and ch.get("tag") == MANAGED_TAG:
                remarks[int(ch["id"])] = _oc_remark(base, data)
    cc_keys = [k["key"].strip() for k in s["cc_keys"] if k.get("key")]
    if cc_keys:
        try:
            adapter = Adapter(CC_ADAPTER_URL, ADAPTER_ADMIN_PASSWORD)
            rows = adapter.usage_query(cc_keys)
            if rows:
                for ch in client.list_channels():
                    if ch.get("name") == "cc-adapter" and ch.get("tag") == MANAGED_TAG:
                        remarks[int(ch["id"])] = _cc_remark(rows[0])
        except Exception:
            pass
    # Codex 渠道：官方用量接口（周窗口/触限状态）
    for ch in client.list_channels():
        if ch.get("type") == 57 and ch.get("tag") == MANAGED_TAG:
            try:
                u = client.codex_usage(int(ch["id"]))
                if u.get("success") and isinstance(u.get("data"), dict):
                    remarks[int(ch["id"])] = _codex_remark(u["data"])
            except Exception:
                pass
    for cid, text in remarks.items():
        try:
            client.update_channel_remark(cid, text)
            out.append({"id": cid, "remark": text})
        except Exception:
            continue
    return out


# ---------------------------------------------------------------- routes

def ensure_policy_tokens(client: Client, s: dict) -> list[dict]:
    """按路由策略创建 auto 分组令牌。

    每个策略 = {名称, 模型列表(逗号分隔), 渠道顺序(逗号分隔, 组名)}：
      - model_limits 限制该令牌只放行指定模型（按模型隔离）
      - auto_groups 顺序 = 渠道尝试顺序（在 new-api 令牌里可直接改）
      - cross_group_retry = 失败自动切下一个组
    这就是 new-api 原生"按模型设置渠道顺序"的实现：模型用哪个令牌，就走哪套顺序。
    """
    out: list[dict] = []
    for p in s.get("policies", []):
        name = str(p.get("name", "")).strip()
        models = str(p.get("models", "")).strip()
        groups = [g.strip() for g in str(p.get("groups", "")).split(",") if g.strip()]
        if not name or not models or not groups:
            continue
        token_name = f"policy-{name}"
        fields = {
            "name": token_name, "expired_time": -1, "remain_quota": 0,
            "unlimited_quota": True, "model_limits_enabled": True,
            "model_limits": models, "group": "auto", "cross_group_retry": True,
            "auto_groups": groups,
        }
        existing = [t for t in client.list_tokens() if t.get("name") == token_name]
        existing.sort(key=lambda t: int(t.get("id", 0)))
        try:
            if existing:
                tid = int(existing[-1]["id"])
                client.update_token(tid, fields)
            else:
                tid = client.add_token(token_name, fields)
            key = client.token_key(tid)
            out.append({"name": token_name, "models": models, "groups": groups, "key": key})
        except Exception as e:
            out.append({"name": token_name, "error": str(e)})
    return out


@app.get("/")
async def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/config")
async def get_config():
    s = load_settings()
    display_url = s["newapi_url"]
    if not display_url or display_url == NEW_API_URL:
        display_url = "http://localhost:3000"  # 容器内地址浏览器不可达，展示成 localhost
    return {
        "newapi_url": display_url,
        "newapi_username": s["newapi_username"],
        "has_newapi_password": bool(s["newapi_password"]),
        "codex_accounts": [{"label": a.get("label", ""),
                            "has_access": bool(a.get("access_token")),
                            "has_refresh": bool(a.get("refresh_token")),
                            "account_id": a.get("account_id", "")} for a in s["codex_accounts"]],
        "opencode_keys": [{"label": k.get("label", ""), "has_key": bool(k.get("key"))} for k in s["opencode_keys"]],
        "cc_keys": [{"label": k.get("label", ""), "has_key": bool(k.get("key"))} for k in s["cc_keys"]],
        "cc_access_key_set": bool(s.get("cc_access_key")),
        "adapter_admin_password_set": bool(ADAPTER_ADMIN_PASSWORD),
        "policies": s.get("policies", []),
    }


@app.post("/api/config")
async def set_config(req: Request):
    raw = await req.json()
    s = load_settings()
    # 只接受白名单字段，防脏写入
    for field in ("newapi_url", "newapi_username", "newapi_password", "cc_access_key"):
        if field in raw:
            s[field] = str(raw[field]).strip() if field != "newapi_url" else str(raw[field]).strip().rstrip("/")
    for field, key_names in (("codex_accounts", ("label", "access_token", "refresh_token", "account_id")),
                             ("opencode_keys", ("label", "key")),
                             ("cc_keys", ("label", "key"))):
        if field in raw and isinstance(raw[field], list):
            cleaned = []
            for item in raw[field]:
                if isinstance(item, dict):
                    cleaned.append({k: str(item.get(k, "")).strip() for k in key_names})
            s[field] = cleaned
    if "policies" in raw and isinstance(raw["policies"], list):
        cleaned_policies = []
        for item in raw["policies"]:
            if isinstance(item, dict):
                cleaned_policies.append({
                    "name": str(item.get("name", "")).strip(),
                    "models": str(item.get("models", "")).strip(),
                    "groups": str(item.get("groups", "")).strip(),
                })
        s["policies"] = [p for p in cleaned_policies if p["name"] and p["models"] and p["groups"]]
    if not s["newapi_url"]:
        s["newapi_url"] = NEW_API_URL
    if not s.get("cc_access_key"):
        s["cc_access_key"] = ADAPTER_ACCESS_KEY
    save_settings(s)
    return {"ok": True}


@app.post("/api/apply")
async def apply():
    s = load_settings()
    report: dict[str, Any] = {"ok": True, "channels": [], "token": "", "errors": [], "warnings": []}

    # 0. 校验
    if not ADAPTER_ADMIN_PASSWORD:
        report["errors"].append("adapter 管理密码未注入（compose 的 CC_ADAPTER_ADMIN_PASSWORD）")

    # 1. new-api：初始化 + 登录（调用一律走容器内地址）
    client = Client(NEW_API_URL)
    try:
        if not s["newapi_username"] or not s["newapi_password"]:
            report["errors"].append("请先填写 new-api 管理员账号")
            return report
        client.setup_and_login(s["newapi_username"], s["newapi_password"])
    except Exception as e:
        report["errors"].append(f"new-api 连接失败: {e}")
        return report

    # 2. 清掉旧的托管渠道（幂等）
    try:
        for ch in client.list_channels():
            if ch.get("tag") == MANAGED_TAG:
                client.delete_channel(int(ch["id"]))
    except Exception as e:
        report["errors"].append(f"清理旧渠道失败: {e}")

    ups_errs: list[str] = []

    # 3. Codex 渠道（每账号一个渠道，便于单独看用量；type=57）
    for i, acc in enumerate(s["codex_accounts"]):
        if not acc.get("access_token"):
            continue
        oauth_key = {
            "type": "codex",
            "access_token": acc["access_token"].strip(),
            "refresh_token": acc.get("refresh_token", "").strip() or None,
            "account_id": acc.get("account_id", "").strip() or "",
            "email": "",
        }
        key = json.dumps({k: v for k, v in oauth_key.items() if v is not None}, ensure_ascii=False)
        mapping = {f"codex/{m}": m for m in CODEX_MODELS}
        # codex 注册裸名 gpt-5.6-luna：responses 客户端（codex CLI）可走
        # codex→opencode→cc 自适应；chat 客户端请用 policy-luna-chat 令牌
        # （官方 chat↔responses 流式转换对 codex SSE 有缺陷，chat 直连 codex 会 500）
        models_all = [f"codex/{m}" for m in CODEX_MODELS] + ADAPTIVE_MODELS
        try:
            cid = client.add_channel({
                "type": 57, "name": f"codex-{acc.get('label') or f'account-{i + 1}'}",
                "key": key, "models": ",".join(models_all),
                "model_mapping": json.dumps(mapping, ensure_ascii=False),
                "tag": MANAGED_TAG, "group": CHANNEL_GROUPS["codex"], "status": 1,
                "priority": CHANNEL_PRIORITY["codex"],
            })
            report["channels"].append({"name": f"codex-{acc.get('label')}", "id": cid, "type": "codex"})
        except Exception as e:
            ups_errs.append(f"codex 渠道({acc.get('label')}): {e}")

    # 4. OpenCode Zen/Go 渠道（type=1）：用 key 探测真实端点(/zen/go/v1 或 /zen/v1)，
    #    以该端点的模型目录作为渠道模型（Go 套餐就是那 26 个），模型前缀统一 oc/
    for i, item in enumerate(s["opencode_keys"]):
        if not item.get("key"):
            continue
        key = item["key"].strip()
        ch_base, probe_base, models = None, None, []
        for cand_ch, cand_probe in OPENCODE_BASES:
            try:
                resp = httpx.get(f"{cand_probe}/models", headers={"Authorization": f"Bearer {key}"}, timeout=15.0)
                if resp.status_code == 200:
                    body = resp.json()
                    ch_base, probe_base = cand_ch, cand_probe
                    data = body.get("data", []) if isinstance(body, dict) else []
                    models = [m.get("id") for m in data if isinstance(m, dict) and m.get("id")]
                    break
            except Exception:
                continue
        if not ch_base:
            ups_errs.append(f"opencode 渠道({item.get('label')}): 无法识别端点（检查 key 或套餐状态）")
            continue
        if not models:
            models = OC_FALLBACK_MODELS
        mapping = {f"oc/{m}": m for m in models}
        models_all = [f"oc/{m}" for m in models] + [m for m in ADAPTIVE_MODELS if m in models]
        try:
            cid = client.add_channel({
                "type": 1, "name": f"opencode-{item.get('label') or f'key-{i + 1}'}",
                "key": key, "base_url": ch_base,
                "models": ",".join(models_all),
                "model_mapping": json.dumps(mapping, ensure_ascii=False),
                "tag": MANAGED_TAG, "group": CHANNEL_GROUPS["opencode"], "status": 1,
                "priority": CHANNEL_PRIORITY["opencode"],
            })
            report["channels"].append({"name": f"opencode-{item.get('label')}", "id": cid,
                                       "type": "opencode", "base": ch_base, "model_count": len(models)})
        except Exception as e:
            ups_errs.append(f"opencode 渠道({item.get('label')}): {e}")

    # 5. Command Code：先把多 key 写进 adapter，再建一个渠道指向 adapter
    cc_models: list[str] = []
    try:
        adapter = Adapter(CC_ADAPTER_URL, ADAPTER_ADMIN_PASSWORD)
        cc_keys = [k["key"].strip() for k in s["cc_keys"] if k.get("key")]
        if cc_keys:
            adapter.set_cc_keys(cc_keys)
            cc_models = adapter.models() or CC_FALLBACK_MODELS
            access = s.get("cc_access_key") or ADAPTER_ACCESS_KEY
            if access:
                mapping = {f"cc/{m}": m for m in cc_models}
                models_all = [f"cc/{m}" for m in cc_models] + [m for m in ADAPTIVE_MODELS if m in cc_models]
                try:
                    cid = client.add_channel({
                        "type": 1, "name": "cc-adapter", "key": access,
                        "base_url": f"{CC_ADAPTER_URL}/v1",
                        "models": ",".join(models_all),
                        "model_mapping": json.dumps(mapping, ensure_ascii=False),
                        "tag": MANAGED_TAG, "group": CHANNEL_GROUPS["cc"], "status": 1,
                        "priority": CHANNEL_PRIORITY["cc"],
                    })
                    report["channels"].append({"name": "cc-adapter", "id": cid, "type": "commandcode"})
                except Exception as e:
                    ups_errs.append(f"cc 渠道: {e}")
        else:
            report["warnings"].append("未配置 Command Code 密钥，跳过 CC 渠道（随时可再加，不影响其余渠道）")
    except Exception as e:
        ups_errs.append(f"adapter 配置: {e}")

    # 5.5 先注册分组倍率（auto 分组令牌的前提；顺序必须在建令牌之前）
    try:
        client.ensure_groups()
    except Exception as e:
        report["errors"].append(f"注册分组倍率失败（auto 分组令牌不可用）: {e}")
        return report

    # 6. 统一令牌（unlimited；auto 分组 + 跨组重试 = 自适应切换；自动清理历史无名令牌）
    try:
        tid, key = client.ensure_token(TOKEN_NAME)
        report["token"] = key
    except Exception as e:
        report["errors"].append(f"创建令牌失败: {e}")

    # 6.5 开启渠道失败自动重试（自适应切换的前提；默认 RetryTimes=0 不重试），
    #     并把 400 加入重试状态码（上游 400 错误也允许切到下个渠道）
    try:
        client.set_option("RetryTimes", str(RETRY_TIMES))
        client.set_option("AutomaticRetryStatusCodes", RETRY_STATUS_CODES)
    except Exception as e:
        report["warnings"].append(f"设置重试参数失败（自适应切换可能不生效）: {e}")

    # 6.6 建路由策略令牌（按模型设置渠道顺序；分组倍率已在 5.5 注册）
    try:
        report["policy_tokens"] = ensure_policy_tokens(client, s)
    except Exception as e:
        report["errors"].append(f"路由策略令牌失败: {e}")

    # 7. 把 OpenCode/CC 额度同步到渠道备注（new-api 渠道列表直接可见；失败不影响主流程）
    try:
        report["remarks"] = sync_remarks(client, s)
    except Exception:
        report["remarks"] = []

    report["errors"].extend(ups_errs)
    report["ok"] = not report["errors"]
    return report


@app.post("/api/sync-remarks")
async def sync_remarks_endpoint():
    s = load_settings()
    try:
        client = Client(NEW_API_URL)
        if not s["newapi_username"] or not s["newapi_password"]:
            return {"ok": False, "message": "未配置 new-api 账号"}
        client.setup_and_login(s["newapi_username"], s["newapi_password"])
        results = sync_remarks(client, s)
        return {"ok": True, "synced": results}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@app.get("/api/balances")
async def balances():
    s = load_settings()
    out: dict[str, Any] = {"codex": [], "cc": [], "opencode": {"status": "none", "accounts": []}}
    try:
        client = Client(NEW_API_URL)
        if not s["newapi_username"] or not s["newapi_password"]:
            out["error"] = "未配置 new-api 账号"
            return out
        client.setup_and_login(s["newapi_username"], s["newapi_password"])
        for ch in client.list_channels():
            if ch.get("tag") != MANAGED_TAG or ch.get("type") != 57:
                continue
            try:
                usage = client.codex_usage(int(ch["id"]))
                row = {"label": ch.get("name"), "success": usage.get("success"),
                       "upstream_status": usage.get("upstream_status"), "data": usage.get("data")}
                if usage.get("success") and isinstance(usage.get("data"), dict):
                    row["summary"] = _codex_remark(usage["data"])
                out["codex"].append(row)
            except Exception as e:
                out["codex"].append({"label": ch.get("name"), "error": str(e)})
    except Exception as e:
        out["error"] = f"new-api 查询失败: {e}"

    try:
        adapter = Adapter(CC_ADAPTER_URL, ADAPTER_ADMIN_PASSWORD)
        keys = [k["key"].strip() for k in s["cc_keys"] if k.get("key")]
        if keys:
            result = adapter.usage_query(keys)
            labels = {k["key"].strip(): k.get("label", "") for k in s["cc_keys"] if k.get("key")}
            for row in result:
                row["label"] = labels.get(row.get("token", ""), "")
            out["cc"] = result
    except Exception as e:
        out["cc_error"] = str(e)

    # OpenCode Go/Zen 余额：官方 /usage 接口（按探测到的端点逐 key 查询）
    oc_accounts: list[dict[str, Any]] = []
    for item in s["opencode_keys"]:
        key = item.get("key", "").strip()
        if not key:
            continue
        row: dict[str, Any] = {"label": item.get("label", ""), "ok": False}
        for cand_ch, cand_probe in OPENCODE_BASES:
            try:
                r = httpx.get(f"{cand_probe}/usage", headers={"Authorization": f"Bearer {key}"}, timeout=15.0)
                if r.status_code == 200:
                    row.update({"ok": True, "base": cand_probe, "data": r.json()})
                    break
                row["error"] = f"{cand_probe}: {r.status_code} {r.text[:100]}"
            except Exception as e:
                row["error"] = f"{cand_probe}: {e}"
        oc_accounts.append(row)
    out["opencode"] = {
        "status": "ok" if any(x.get("ok") for x in oc_accounts) else "error",
        "accounts": oc_accounts,
        "note": "数据来自 opencode.ai 官方 /zen/go/v1/usage（Go）或 /zen/v1/usage（Zen）",
    }
    return out


@app.get("/api/status")
async def status():
    s = load_settings()
    adapter = Adapter(CC_ADAPTER_URL, ADAPTER_ADMIN_PASSWORD)
    health = adapter.health()
    newapi_up = False
    try:
        r = httpx.get(f"{NEW_API_URL}/api/setup", timeout=5.0)
        newapi_up = r.status_code < 500
        setup_state = r.json().get("data", {}) if r.status_code < 500 else {}
    except Exception:
        setup_state = {}
    return {
        "newapi": {"url": s["newapi_url"], "up": newapi_up, "setup": setup_state.get("status", None),
                   "root_init": setup_state.get("root_init", None)},
        "adapter": health,
        "configured": {"codex": len([a for a in s["codex_accounts"] if a.get("access_token")]),
                       "opencode": len([k for k in s["opencode_keys"] if k.get("key")]),
                       "cc": len([k for k in s["cc_keys"] if k.get("key")])},
    }


@app.post("/api/test-codex")
async def test_codex_endpoint(req: Request):
    """对每个 codex 渠道执行流式测试。

    new-api 面板测试默认非流式 -> codex 后端必报 'Stream must be set to true'，
    这里强制 stream=true（等价于面板测试对话框里打开 Stream Mode）。
    """
    raw = await req.json()
    model = str(raw.get("model") or "codex/gpt-5.6-luna")
    s = load_settings()
    try:
        client = Client(NEW_API_URL)
        if not s["newapi_username"] or not s["newapi_password"]:
            return {"ok": False, "message": "未配置 new-api 账号"}
        client.setup_and_login(s["newapi_username"], s["newapi_password"])
        results: list[dict] = []
        for ch in client.list_channels():
            if ch.get("type") != 57 or ch.get("tag") != MANAGED_TAG:
                continue
            try:
                resp = client.c.get(f"/api/channel/test/{ch['id']}",
                                    headers=client._h(),
                                    params={"stream": "true", "model": model})
                if resp.headers.get("content-type", "").startswith("application/json"):
                    body = resp.json()
                    results.append({"name": ch.get("name"), "id": ch["id"],
                                    "success": bool(body.get("success")),
                                    "time": body.get("time"),
                                    "message": str(body.get("message") or "")[:200]})
                else:
                    results.append({"name": ch.get("name"), "id": ch["id"], "success": False,
                                    "message": f"非 JSON 响应 {resp.status_code}: {resp.text[:120]}"})
            except Exception as e:
                results.append({"name": ch.get("name"), "id": ch["id"], "success": False, "message": str(e)[:200]})
        return {"ok": True, "model": model, "results": results}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@app.get("/api/hint")
async def hint():
    return {"codex": "在 ~/.codex/auth.json 的 tokens 里取 refresh_token / access_token（用 codex login 登录后生成）",
            "zen": "登录 opencode.ai → 账户 → API keys 复制（Go/Zen 订阅都在这；Go 套餐走 /zen/go 端点，自动识别）",
            "cc": "在 https://commandcode.ai 设置页复制 API Key（形如 user_xxx）"}


if __name__ == "__main__":
    import uvicorn
    ensure_dir()
    uvicorn.run(app, host="0.0.0.0", port=8888)