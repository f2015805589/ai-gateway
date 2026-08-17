# 客户端接入网关

所有客户端统一用你 new-api 面板里生成的那个**令牌** + `http://localhost:3000`，就能用到你全部账号。

| 客户端 | 配置位置 | 说明 |
|---|---|---|
| Codex CLI | `~/.codex/config.toml` | 用 `clients/codex.config.toml`，`wire_api = "responses"`，只能调用走 `/v1/responses` 的模型 |
| OpenCode CLI | `~/.config/opencode/opencode.json` | 用 `clients/opencode.json`，`@ai-sdk/openai-compatible` 走 `/v1/chat/completions` |
| Cline / Cherry Studio 等 GUI | 设置里填 base_url + api_key | base_url = `http://localhost:3000/v1`，key = 令牌 |

## 模型命名约定（重要）

不同渠道可能卖同名模型（比如 codex 渠道和 zen 渠道都有 `gpt-5.6-sol`），所以**在绑渠道时给每个渠道的模型加前缀**，请求时用带前缀的名字：

- `codex/...` —— new-api 原生 Codex 渠道（`codex/gpt-5.6-sol`）
- `zen/...` —— OpenCode Zen/Go 渠道（`zen/deepseek-v4-flash`）
- `cc/...` —— Command Code 渠道（`cc/claude-sonnet-4-6`）

实现方式见主 README 第 3 节的"模型重定向"。

## 协议边界（决定哪些客户端能用哪些模型）

| 客户端走的协议 | 可用模型 |
|---|---|
| `/v1/responses`（Codex CLI、支持 responses 的 SDK） | Codex 渠道全部模型；zen 渠道的 GPT/Grok 系 |
| `/v1/chat/completions`（OpenCode、Cline 等） | zen 渠道的 DeepSeek/Kimi/GLM/MiniMax 系；CC 渠道全部模型 |
| `/v1/messages`（Anthropic 系客户端） | adapter 也提供，但官方说基础适配；优先走 chat/completions |

想在一个客户端里用尽三个账号：**opencode CLI 是最佳载体**（chat/completions 兼容 zen 便宜模型 + CC 全部模型；再用 claude code 或其他支持 responses 的工具消费 codex 额度）。