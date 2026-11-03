"""命令行：python -m finaudit.cli run --fixtures fixtures/demo01 --out ../out/demo01"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finaudit")
    parser.add_argument("--version", action="version", version=f"finaudit {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="运行 S0–S7 并生成 O1/O2/O3")
    run_p.add_argument("--fixtures", required=True)
    run_p.add_argument("--out", required=True)
    run_p.add_argument("--llm-provider", default="", choices=["", "mock", "openai"],
                       help="mock=确定性占位；openai=OpenAI 兼容端点；留空按 preset")
    run_p.add_argument("--llm-preset", default="",
                       choices=["", "mock", "local", "modelscope", "deepseek"],
                       help="模型预设：mock=离线占位；local=本机模型；modelscope/deepseek=外部（需授权）")
    run_p.add_argument("--llm-model", default="")
    run_p.add_argument("--llm-model-version", default="")
    run_p.add_argument("--llm-base-url", default="")
    run_p.add_argument("--allow-external-llm", action="store_true",
                       help="授权把研报正文发往外部模型端点（modelscope/deepseek 等）；不授权则拒绝发送")
    demo_p = sub.add_parser("demo", help="运行并输出 D0–D8 演示记录")
    demo_p.add_argument("--fixtures", required=True)
    demo_p.add_argument("--out", required=True)
    demo_p.add_argument("--llm-preset", default="",
                        choices=["", "mock", "local", "modelscope", "deepseek"])
    demo_p.add_argument("--llm-model", default="")
    demo_p.add_argument("--llm-base-url", default="")
    demo_p.add_argument("--allow-external-llm", action="store_true",
                        help="授权把研报正文发往外部模型端点")
    sub.add_parser("roster", help="打印运行时 Agent 名册（RA1–RA8 与权限边界）")
    doc_p = sub.add_parser("doctor", help="模型连通性自检（把网关错误翻译成结论）")
    doc_p.add_argument("--preset", default="modelscope",
                       choices=["mock", "local", "modelscope", "deepseek"])
    doc_p.add_argument("--model", default="")
    doc_p.add_argument("--base-url", default="")
    doc_p.add_argument("--allow-external-llm", action="store_true",
                        help="授权自检时向外部端点发送一条最小请求")
    ver_p = sub.add_parser("verify", help="核对输出目录哈希，确认交付包未被改动")
    ver_p.add_argument("--out", required=True, help="要核对的任务输出目录")
    ver_p.add_argument("--json", action="store_true", dest="as_json",
                       help="输出 JSON，便于脚本消费")
    args = parser.parse_args(argv)

    if args.cmd == "demo":
        from .demo import run_demo
        from .llm import LLMConfig
        from .presets import resolve
        cfg = None
        if args.llm_preset:
            preset = resolve(args.llm_preset, model=args.llm_model, base_url=args.llm_base_url)
            cfg = LLMConfig(provider=preset["provider"], model=preset["model"],
                            model_version=preset.get("model_version", "unknown"),
                            base_url=preset["base_url"],
                            extra_body=preset.get("extra_body", {}),
                            allow_external=args.allow_external_llm)
        run_demo(args.fixtures, args.out, llm_config=cfg)
        return 0
    if args.cmd == "roster":
        from .agents import roster
        for r in roster():
            print(f"{r['code']:7s} {r['name']}｜阶段 {r['stage']}")
            print(f"        读 {','.join(r['reads'])}")
            print(f"        写 {','.join(r['writes'])}")
            print(f"        禁 {'; '.join(r['forbidden'])}")
        return 0
    if args.cmd == "doctor":
        from .doctor import probe
        ok, message = probe(args.preset, args.model, args.base_url,
                        allow_external=args.allow_external_llm)
        print(("✅ " if ok else "❌ ") + message)
        return 0 if ok else 1

    if args.cmd == "verify":
        from pathlib import Path

        from .verify import verify_output
        result = verify_output(Path(args.out))
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            def _pad(text: str, width: int) -> str:
                # 中文按两格算，避免路径列与状态列错位
                shown = sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)
                return text + " " * max(0, width - shown)

            for c in result["checks"]:
                mark = "OK  " if c["status"] == "一致" else "FAIL"
                print(f"{mark} {_pad(c['file'], 30)} {c['status']}")
            for name in result["missing"]:
                print(f"FAIL {_pad(name, 30)} 缺失")
            for name in result.get("unexpected") or []:
                print(f"WARN {_pad(name, 30)} 未登记（不属于本次运行）")
            print(("✅ " if result["ok"] else "❌ ") + result["note"])
        return 0 if result["ok"] else 1

    if args.cmd == "run":
        from .llm import LLMConfig
        from .pipeline import run
        from .presets import resolve
        cfg = None
        if args.llm_preset:
            preset = resolve(args.llm_preset, model=args.llm_model, base_url=args.llm_base_url)
            cfg = LLMConfig(provider=preset["provider"], model=preset["model"],
                            model_version=preset.get("model_version", "unknown"),
                            base_url=preset["base_url"],
                            extra_body=preset.get("extra_body", {}),
                            allow_external=args.allow_external_llm)
        elif args.llm_provider or args.llm_model or args.llm_base_url:
            cfg = LLMConfig(
                provider=args.llm_provider or "openai",
                model=args.llm_model or "configured-model",
                model_version=args.llm_model_version or "unknown",
                base_url=args.llm_base_url,
                allow_external=args.allow_external_llm,
            )
        summary = run(args.fixtures, args.out, llm_config=cfg)
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return 0
    return 1


if __name__ == "__main__":
    # 输入不合契约、材料缺失这类是可预期的使用错误，给一行结论即可；
    # 抛整段回溯只会把真正的原因埋在第 30 行。
    from .llm import LLMError
    try:
        sys.exit(main())
    except (ValueError, LLMError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        sys.exit(2)
