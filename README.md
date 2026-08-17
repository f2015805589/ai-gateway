# 个人 AI 网关 —— 一个链接、一个令牌，用尽你的全部账号

把散落的账号（**Codex / OpenCode Zen·Go / Command Code**）统一成一个 OpenAI 兼容 API：

- 管理前端：http://localhost:8888（在这里填账号，**不需要编辑任何配置文件**）
- 统一入口：`http://localhost:3000/v1` + 一个令牌（管理台一键生成）
- 余额/用量：管理台里直接看（Codex 按账号、Command Code 按 key）

```
                管理台 http://localhost:8888（填账号+看余额）
                              │
                     ┌────────▼────────┐        ┌──────────────┐
                     │  manager 编排服务  │━━━━━━▶│ cc-adapter    │
                     └────────┬────────┘ 自动   │ (Command Code │
                              │ 建渠道/令牌    │  多 key 负载均衡)│
                     ┌────────▼────────┐        └──────┬───────┘
                     │    new-api 网关   │◀─────────────┘
                     │ (3000 API / 3001 面板)│        api.commandcode.ai
                     └───┬───────┬───────┘
               Codex 渠道    OpenCode 渠道
               (codex 账号)   (/zen/go 或 /zen/v1，自动识别)
```

## 1. 部署（一条命令，无需手工配置）

**最简单：双击 `start.cmd`**（或 `setup.ps1`）。

命令行方式二选一：

```powershell
# 方式 A：cmd 命令行
powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1

# 方式 B：PowerShell 里
Set-ExecutionPolicy -Scope Process Bypass; .\setup.ps1
```

脚本会：自动生成随机密钥（写进 .env，你不用编辑）→ 克隆 command-code-adapter → 构建镜像 → 启动三个容器 → 自动打开管理台 http://localhost:8888。

**停止：双击 `stop.cmd`**（容器停止，`./data` 数据保留；想彻底清空再加 `docker compose down -v`）。重启随时再双击 `start.cmd`。

### 从 GitHub 部署（clone 后直接跑）

本仓库**不需要补任何文件**：`setup.ps1` 会自动生成 `.env`（随机密钥）、自动 clone `cc-adapter`、容器首次启动自动建库。跑起来后：

- **方式 A（全新账号）**：直接在管理台 http://localhost:8888 填 Codex / OpenCode / Command Code 账号 → 一键应用
- **方式 B（恢复已有配置）**：把私有配置仓库 `ai-gateway-private` 的两个文件复制进来，即可完整恢复：
  ```powershell
  copy ..\ai-gateway-private\.env .\
  copy ..\ai-gateway-private\data\manager\settings.json .\data\manager\
  ```
  然后直接运行 setup.ps1（`.env` 已存在则不会覆盖）。

> 唯一注意：`settings.json` 里的 `cc_access_key` 必须与 `.env` 的 `CC_ADAPTER_ACCESS_KEY` 一致（两个文件一起复制即可保证；全新部署时管理台会自动用新生成的值）。

## 2. 在管理台填账号（全部在网页里完成）

1. **第 0 步**：填 new-api 管理员账号（首次自动帮你初始化 root 管理员并登录）。
2. **第 1 步**：Codex 账号 —— 每行一个：备注名 + `access_token` / `refresh_token` / `account_id`（取自 `~/.codex/auth.json` 的 tokens，需先 `codex login`）。每个账号一个独立渠道，可单独看用量。
3. **第 2 步**：OpenCode —— 每行一个 API key（登录 opencode.ai → 账户 → API keys）。**Go 套餐和 Zen 套餐都填这里**：应用时管理台会用你的 key 自动探测端点（Go 走 `/zen/go`、Zen 走 `/zen/v1`）并按你套餐的模型目录建渠道，不用你区分。
4. **第 3 步**：Command Code —— 每行一个 key（commandcode.ai 设置页，形如 `user_xxx`），多个 key 自动写入适配器负载均衡。
5. 点 **“一键应用”** → 自动创建所有渠道 + 生成统一令牌。
6. “刷新余额/用量”查看：Codex 各账号用量、OpenCode 各 key 的官方 /usage 数据（Go=/zen/go、Zen=/zen）、Command Code 各 key 的套餐/额度/5 小时窗口。
   > 额度同步是**事件驱动**的：在 new-api 面板（3001）里点渠道“测试”，manager 会在数秒内自动把 OpenCode/CC 额度刷进渠道备注列；管理台也有手动同步按钮。如需周期性兜底，可设环境变量 `SYNC_INTERVAL_SECONDS`（默认 0=关闭）。

> 所有凭据只保存在本机挂载卷（`./data/`）与各自服务内部，不上传任何地方。
> 渠道均带 `gw-managed` 标签，重复应用会先清掉旧渠道再重建（幂等）。

## 3. 客户端接线（全平台同一个 base_url + 令牌）

| 客户端 | 配置 |
|---|---|
| Codex CLI | `clients/codex.config.toml`（`~/.codex/config.toml`，`wire_api = "responses"`） |
| OpenCode CLI | `clients/opencode.json`（`~/.config/opencode/opencode.json`，走 chat/completions） |
| Cline / Cherry Studio 等 | base_url = `http://localhost:3000/v1`，key = 管理台生成的令牌 |

模型命名（在渠道里已配好重定向，客户端直接请求带前缀的名字）：

- `codex/gpt-5.6-sol`、`codex/gpt-5.5` …（Codex 账号）
- `oc/deepseek-v4-flash`、`oc/kimi-k2.7-code`、`oc/gpt-5.6-luna` …（OpenCode Go/Zen，应用时自动按你套餐的模型目录生成）
- `cc/claude-sonnet-4-6`、`cc/deepseek-v4-flash` …（Command Code）

**✨ 自适应模型 `gpt-5.6-luna`**：这个模型名自动注册到 OpenCode / Command Code 两个渠道（chat 与 responses 双协议都通），顺序由渠道**优先级**字段控制（默认 OpenCode 2 > CC 1，数字大先试），前者限流/失败自动切换下一个（一键应用已把 new-api 重试次数设为 3、并把 400 加入重试状态码）。**想调整顺序，直接在 new-api 渠道编辑里改"优先级"即可**。
**Codex 说明**：官方 new-api 的 chat→responses 流式转换对 codex SSE 有解析缺陷（非流式正常、流式 500 且不可重试），所以 codex 不注册裸自适应名，而是用前缀名 `codex/gpt-5.6-luna`（供 codex CLI 等 responses 客户端，实测正常）。

**路由策略（按模型设置渠道顺序）**：管理台第 4 步可配置多条策略（模型列表 + 渠道顺序），每条策略生成一个专用令牌 `policy-<名称>`：

- 令牌只放行策略中的模型（模型限制），请求按 auto 分组顺序尝试渠道组，失败自动切换（跨组重试）
- **改顺序两条路**：① new-api → 令牌 → 编辑 `policy-xxx` → auto 分组顺序（拖拽/改列表）；② 管理台改策略后一键应用
- 默认策略：`gpt-5.6-luna → opencode,cc`；想加 `codex` 组到顺序里需先把 codex 渠道注册裸名（见上一条说明的限制）

**协议边界**（决定哪些客户端能用哪些模型）：

| 客户端走的协议 | 可用模型 |
|---|---|
| `/v1/responses`（Codex CLI） | codex 渠道全部模型；OpenCode 的 GPT/Grok 系 |
| `/v1/chat/completions`（OpenCode、Cline 等） | OpenCode 的 DeepSeek/Kimi/GLM/MiniMax/Qwen 系（Go 套餐目录 26 个）；cc 渠道全部模型 |
| `/v1/messages`（Anthropic 系） | cc 适配器支持（基础适配）；优先 chat/completions |

**Codex 渠道的验证方式**（重要）：

- new-api 的快捷"测试"按钮**对 Codex 渠道必然报错**（`Stream must be set to true`）：它发的是**非流式**请求，而 codex 官方后端只接受流式。这不是渠道坏了。
- **正确姿势**：在渠道操作里打开**测试对话框**（能选模型的那个）→ 把 **Stream Mode 开关打开（Enabled）** → 再测，即可通过（已实测 ~2.7s 成功）。
- 其他验证：① 渠道"用量"弹窗能拉到账号数据（plan、rate_limit、credits）；② 用 codex CLI 配好 `clients/codex.config.toml` 正常跑；③ 直接调 `POST /v1/responses`（`stream: true`）。
- ChatGPT Plus 账号显示 `balance: 0 / has_credits: false` 是**正常**的：Plus 是每月限流制不是充值制，看 `rate_limit.allowed` / 用量弹窗里的窗口即可。

想把三个账号用在一个客户端里：**opencode CLI 最合适**（chat/completions 覆盖 zen 便宜模型 + CC 全部）；codex 额度留给 codex CLI 或支持 responses 的工具。

## 4. 各服务端口

| 端口 | 服务 | 说明 |
|---|---|---|
| 8888 | 管理台 | 默认只绑 127.0.0.1（本机） |
| 3000 | new-api REST | 统一 API 入口 |
| 3001 | new-api 面板 | 可看明细/日志/计费 |
| 8080 | cc-adapter | 适配器（含 /admin 面板） |

## 5. 扩展路线

1. **本地**（默认）：管理台只绑本机，安全。
2. **局域网**：把 compose 里 `127.0.0.1:8888:8888` 与 `3000/3001/8080` 的映射改成 `0.0.0.0` 或指定网卡 IP，放行防火墙。
3. **对外 / 内网穿透**，二选一：
   - **Tailscale（最省事）**：所有设备进同一 tailnet，直连 `http://<机器ip>:3000`，自带加密。
   - **cloudflared / frp + Caddy**：反代 3000/3001 + HTTPS；对外前务必确认设置了管理台与各面板强密码。

## 6. 安全提醒

- `./data/`（含 new-api 数据库与 manager 凭据）别有别人能读取的权限，别提交 git。
- 管理台 8888 默认仅本机；对外暴露前设置好 new-api 密码和 adapter 密钥。
- 本项目是“网关编排”层，账号数据最终都存于 new-api / cc-adapter 自己；删掉 `manager/data/settings.json` 只会重置管理台表单，不丢渠道。

## 源码参考

- [QuantumNous/new-api](https://github.com/QuantumNous/new-api)（网关 + 面板，Codex 渠道为原生 57 号类型）
- [dgqyushen/command-code-adapter](https://github.com/dgqyushen/command-code-adapter)（Command Code 适配器，管理 API：`/admin/api/*`）
- opencode.ai/zen（OpenCode Zen/Go：渠道 base 用 `https://opencode.ai/zen/go`（Go）或 `https://opencode.ai/zen`（Zen），new-api 会自动拼接 `/v1/chat/completions`）
- ⚠️ opencode.ai 的生成接口**只接受 HTTP/2**：new-api 的 Go 中继自动协商，无需配置；但自己写脚本直连时必须用支持 HTTP/2 的客户端（`curl --http2`）
- [slkiser/opencode-quota](https://github.com/slkiser/opencode-quota)（可选：OpenCode 配额加强插件，管理台已内置官方 /usage 直查）