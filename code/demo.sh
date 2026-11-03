#!/usr/bin/env bash
# 一键复现：单元测试 → 端到端运行 → 列出交付物
# 用法：bash demo.sh
#       FINAUDIT_LLM_MODEL=<模型名> FINAUDIT_LLM_BASE_URL=<端点> bash demo.sh   # 接真实模型
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python}"

echo "== 0/3 环境 =="
"$PY" --version

echo "== 1/3 单元测试 =="
"$PY" -m unittest discover -s tests -q

echo "== 2/3 端到端运行（S0–S7）+ 演示记录（D0–D8）=="
LLM_ARGS=()
if [ -n "${FINAUDIT_LLM_MODEL:-}" ]; then
  LLM_ARGS=(--llm-provider openai
            --llm-base-url "${FINAUDIT_LLM_BASE_URL:-}"
            --llm-model "${FINAUDIT_LLM_MODEL}")
  echo "   LLM: ${FINAUDIT_LLM_MODEL} @ ${FINAUDIT_LLM_BASE_URL:-<unset>}"
else
  echo "   LLM: mock（确定性占位；设置 FINAUDIT_LLM_MODEL/BASE_URL 可接真实模型）"
fi
"$PY" -m finaudit.demo --fixtures fixtures/demo01 --out ../out/demo01

if "$PY" -c "import pypdf" >/dev/null 2>&1; then
  echo "== 2b/3 PDF 材料路径（pypdf）=="
  "$PY" -m finaudit.cli run --fixtures fixtures/demo01_pdf --out ../out/demo01_pdf "${LLM_ARGS[@]}" >/dev/null
  echo "   PDF 路径完成：../out/demo01_pdf"
else
  echo "== 2b/3 未安装 pypdf，跳过 PDF 路径（pip install -r requirements.txt 可启用）=="
fi

if [ "${FINAUDIT_OCR_TEST:-}" = "1" ] && "$PY" -c "import rapidocr_onnxruntime" >/dev/null 2>&1; then
  echo "== 2c/3 扫描件 OCR 路径（RapidOCR，约 2 分钟）=="
  "$PY" -m finaudit.cli run --fixtures fixtures/demo01_scan --out ../out/demo01_scan "${LLM_ARGS[@]}" >/dev/null
  echo "   扫描件路径完成：../out/demo01_scan"
else
  echo "== 2c/3 跳过扫描件路径（需 rapidocr_onnxruntime 且设置 FINAUDIT_OCR_TEST=1）=="
fi

echo "== 3/3 交付物 =="
find ../out/demo01 -maxdepth 2 -type f | sort
echo
echo "O1 报告：../out/demo01/o1/O1-核查报告.md"
echo "评测报告：../out/demo01/eval-report.md"
echo "审计包：../out/demo01/o3/manifest.json"
