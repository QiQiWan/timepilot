"""Curated LLM provider presets; URLs documented by official vendor references.

The catalogue only provides suggested endpoint settings. Models and entitlements
change; model IDs remain editable, and no remote request occurs upon selection.
"""
from __future__ import annotations

# Public server metadata. Never include API keys in this catalogue.
PROVIDERS = [
    {"id": "openai", "name": "OpenAI", "region": "国际", "protocol": "openai",
     "base_url": "https://api.openai.com/v1", "models": ["gpt-4o"],
     "docs": "https://platform.openai.com/docs/api-reference/chat", "tip": "模型 ID 请以控制台实际开通情况为准。"},
    {"id": "deepseek", "name": "DeepSeek", "region": "国内", "protocol": "openai",
     "base_url": "https://api.deepseek.com", "models": ["deepseek-flash", "deepseek-v4-pro"],
     "docs": "https://api-docs.deepseek.com/quick_start/agent_integrations/", "tip": "使用 DeepSeek 开放平台 API Key。"},
    {"id": "qwen-cn", "name": "阿里云百炼 · 千问（北京）", "region": "国内", "protocol": "openai",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "models": ["qwen-plus"],
     "docs": "https://help.aliyun.com/zh/model-studio/base-url", "tip": "推荐在生产环境替换为对应地域的业务空间专属地址。"},
    {"id": "qwen-intl", "name": "阿里云百炼 · 千问（新加坡）", "region": "国际", "protocol": "openai",
     "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "models": ["qwen-plus"],
     "docs": "https://help.aliyun.com/zh/model-studio/base-url", "tip": "API Key 与地域必须一致，不能跨区域使用。"},
    {"id": "zhipu", "name": "智谱 GLM · BigModel", "region": "国内", "protocol": "openai",
     "base_url": "https://open.bigmodel.cn/api/paas/v4", "models": ["glm-5.3-flash"],
     "docs": "https://docs.bigmodel.cn/", "tip": "填写普通通用 API Key；编程套餐有不同专用地址。"},
    {"id": "kimi", "name": "Kimi · Moonshot", "region": "国内", "protocol": "openai",
     "base_url": "https://api.moonshot.cn/v1", "models": ["kimi-k2.5"],
     "docs": "https://platform.kimi.com/docs/api/overview", "tip": "根据模型权限填写准确模型 ID。"},
    {"id": "doubao", "name": "字节跳动 · 火山方舟", "region": "国内", "protocol": "openai",
     "base_url": "https://ark.cn-beijing.volces.com/api/v3", "models": ["doubao-seed-2-1-pro-260915"],
     "docs": "https://docs.volcengine.com/docs/ark/base-url-and-authentication?lang=zh", "tip": "按控制台开通的模型或推理接入点填写 ID。"},
    {"id": "gemini", "name": "Google · Gemini", "region": "国际", "protocol": "openai",
     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "models": ["gemini-3.8-flash"],
     "docs": "https://ai.google.dev/gemini-api/docs/openai", "tip": "使用 Gemini API Key；此处调用 OpenAI 兼容端点。"},
    {"id": "anthropic", "name": "Anthropic · Claude", "region": "国际", "protocol": "anthropic",
     "base_url": "https://api.anthropic.com/v1", "models": [],
     "docs": "https://platform.claude.com/docs/en/api/messages", "tip": "采用 Anthropic Messages 协议；模型 ID 由账户决定。"},
    {"id": "openrouter", "name": "OpenRouter", "region": "国际", "protocol": "openai",
     "base_url": "https://openrouter.ai/api/v1", "models": [],
     "docs": "https://openrouter.ai/docs", "tip": "模型 ID 一般采用 provider/model 格式。"},
    {"id": "groq", "name": "GroqCloud", "region": "国际", "protocol": "openai",
     "base_url": "https://api.groq.com/openai/v1", "models": ["llama-3.1-8b-instant"],
     "docs": "https://console.groq.com/docs/openai", "tip": "根据当前活跃模型列表配置 ID。"},
    {"id": "ollama", "name": "Ollama · 本地模型", "region": "本地", "protocol": "openai",
     "base_url": "http://127.0.0.1:11434/v1", "models": [],
     "docs": "https://docs.ollama.com/api/openai-compatibility", "tip": "填入本机已经下载的模型名称；本地通常不需要密钥。"},
    {"id": "custom", "name": "自定义 OpenAI 兼容 API", "region": "自定义", "protocol": "openai",
     "base_url": "https://api.example.com/v1", "models": [],
     "docs": "", "tip": "仅支持已授权的 HTTPS 地址，或本机 loopback HTTP 服务。"},
]
PROVIDERS_BY_ID = {p["id"]: p for p in PROVIDERS}


def public_catalogue() -> list[dict]:
    return [dict(item) for item in PROVIDERS]
