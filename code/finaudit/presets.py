"""模型预设：把可用端点/模型固化进项目，一条命令切换。

密钥一律走环境变量（FINAUDIT_LLM_API_KEY），不入代码、不入配置文件。

数据外发口径（egress）：
- `none`：不出本机（mock 占位）；
- `local`：本机/内网端点（如 Ollama 127.0.0.1），材料不出本组织网络边界；
- `external`：发送至第三方服务，研报正文会离开本机，**须显式授权**（Web 勾选确认 /
  CLI `--allow-external-llm`），未授权时拒绝发送，不静默降级。

选择依据（2026-10-18 魔搭可用清单 + 中文金融抽取任务）：
- modelscope 首选 deepseek-ai/DeepSeek-V4-Pro-0813（Pro 最新版，长上下文与中文结构化输出强），
  备选按能力/成本递减排列；运行失败可用 --llm-model 显式覆盖。
"""

from __future__ import annotations

from urllib.parse import urlparse

PRESETS: dict[str, dict] = {
    "mock": {
        "provider": "mock",
        "base_url": "",
        "model": "mock-claim-extractor",
        "model_version": "0.1.0",
        "egress": "none",
        "note": "离线确定性占位，仅演示/测试；材料不出本机",
    },
    "local": {
        "provider": "openai",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:14b",
        "model_version": "local",
        "extra_body": {},
        "egress": "local",
        "note": "本机模型（Ollama 等 OpenAI 兼容端点）；材料不出本机，需先自行起服务",
    },
    "modelscope": {
        "provider": "openai",
        "base_url": "https://api-inference.modelscope.cn/v1",
        "model": "deepseek-ai/DeepSeek-V4-Pro-0813",
        "model_version": "0813",
        "extra_body": {"thinking": {"type": "disabled"}},
        "egress": "external",
        "fallbacks": [
            "ZhipuAI/GLM-5.2",
            "Qwen/Qwen3.5-397B-A17B",
            "deepseek-ai/DeepSeek-V4.1-Flash",
        ],
        "note": "魔搭社区（OpenAI 兼容）；需账号完成邮箱验证；默认关闭思考以稳定输出 JSON"
               "；研报正文将发送至魔搭，需授权",
    },
    "deepseek": {
        "provider": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "model_version": "chat",
        "extra_body": {},
        "egress": "external",
        "fallbacks": ["deepseek-reasoner"],
        "note": "DeepSeek 官方；按量计费，余额不足返回 402；研报正文将发送至 DeepSeek，需授权",
    },
}

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def egress_of(base_url: str) -> str:
    """按端点判定外发口径：本机/内网为 local，其余为 external，空为 none。"""
    if not base_url:
        return "none"
    host = (urlparse(base_url).hostname or "").lower()
    if host in LOCAL_HOSTS or host.endswith((".local", ".internal")):
        return "local"
    return "external"


def resolve(preset: str, *, model: str = "", base_url: str = "") -> dict:
    if preset not in PRESETS:
        raise KeyError(f"未知预设: {preset}（可选 {','.join(PRESETS)}）")
    cfg = dict(PRESETS[preset])
    if model:
        cfg["model"] = model
    if base_url:
        cfg["base_url"] = base_url
    # 显式覆盖端点后，外发口径按实际端点重判（不沿用预设标注）
    cfg["egress"] = egress_of(cfg["base_url"]) if cfg["provider"] == "openai" else "none"
    return cfg


def describe() -> list[dict]:
    """供界面/文档列出的预设清单（含外发口径）。"""
    return [{"name": k, "model": v["model"], "base_url": v["base_url"],
             "egress": v["egress"], "note": v["note"]} for k, v in PRESETS.items()]
