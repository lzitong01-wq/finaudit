"""模型连通性自检（doctor）：把网关错误翻译成可执行结论。

用法：
    python -m finaudit.doctor --preset modelscope
    python -m finaudit.doctor --preset deepseek
    python -m finaudit.doctor --preset modelscope --model Qwen/Qwen3.5-397B-A17B

退出码：0 可用；1 不可用（打印原因与处置建议）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from .presets import resolve

DIAGNOSIS = {
    401: ("密钥无效或未携带", "检查环境变量 FINAUDIT_LLM_API_KEY 是否为该平台签发的 key"),
    402: ("账户余额不足", "到对应平台充值后再试"),
    403: ("权限/账号状态受限", "常见原因：未完成邮箱验证、未申请该模型权限、IP 白名单；"
                              "按返回正文提示在平台侧完成"),
    404: ("模型或路径不存在", "用 /v1/models 列出可用模型，确认 --llm-model 拼写"),
    429: ("限流", "降低并发或稍后重试"),
}


def probe(preset: str, model: str = "", base_url: str = "",
          timeout: int = 60, allow_external: bool = False) -> tuple[bool, str]:
    cfg = resolve(preset, model=model, base_url=base_url)
    if cfg["provider"] == "mock":
        return True, "mock 预设：离线可用（非真实模型）"
    if cfg["egress"] == "external" and not allow_external:
        return False, ("外部端点未授权：自检会向该端点发送一条最小请求。"
                       "确认后加 --allow-external-llm；或改用本机预设 local")
    key = os.environ.get("FINAUDIT_LLM_API_KEY", "")
    if not key:
        return False, "未设置 FINAUDIT_LLM_API_KEY"
    body = json.dumps({
        "model": cfg["model"],
        "messages": [{"role": "user", "content": "只回复两个字：可用"}],
        "max_tokens": 16, "temperature": 0,
    }).encode("utf-8")
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return True, f"可用｜model={cfg['model']}｜回复={content.strip()[:20]}"
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "ignore")[:200]
        except Exception:  # noqa: BLE001
            pass
        reason, advice = DIAGNOSIS.get(exc.code, (f"HTTP {exc.code}", "查看返回正文"))
        return False, f"{reason}｜{advice}｜服务端原文：{detail or exc.reason}"
    except Exception as exc:  # noqa: BLE001 - 超时/网络
        return False, f"网络或超时：{exc}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finaudit.doctor")
    parser.add_argument("--preset", default="modelscope")
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    args = parser.parse_args(argv)
    ok, message = probe(args.preset, args.model, args.base_url)
    print(("✅ " if ok else "❌ ") + message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
