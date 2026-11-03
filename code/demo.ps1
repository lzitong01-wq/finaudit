# 一键复现（PowerShell）：单元测试 -> 端到端运行（D0-D8 演示记录）-> PDF 路径 -> 交付物清单
# 用法：powershell -ExecutionPolicy Bypass -File demo.ps1
# 接真实模型：$env:FINAUDIT_LLM_API_KEY="***"; powershell -File demo.ps1 -Preset modelscope
param(
    [string]$Preset = ""    # mock / modelscope / deepseek；空=mock 离线占位
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = if ($env:PYTHON) { $env:PYTHON } else { "python" }

Write-Host "== 0/3 环境 =="
& $py --version

Write-Host "== 1/3 单元测试 =="
& $py -m unittest discover -s tests -q

Write-Host "== 2/3 端到端运行（S0-S7）+ 演示记录（D0-D8）=="
$demoArgs = @("--fixtures", "fixtures/demo01", "--out", "../out/demo01")
if ($Preset) {
    $demoArgs += @("--llm-preset", $Preset)
    Write-Host "   LLM 预设: $Preset"
} else {
    Write-Host "   LLM: mock（确定性占位；-Preset modelscope 可接真实模型）"
}
& $py -m finaudit.cli demo @demoArgs

Write-Host "== 2b/3 PDF 材料路径 =="
& $py -c "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('pypdf') else 1)"
if ($LASTEXITCODE -eq 0) {
    & $py -m finaudit.cli run --fixtures fixtures/demo01_pdf --out ../out/demo01_pdf | Out-Null
    Write-Host "   PDF 路径完成：../out/demo01_pdf"
} else {
    Write-Host "   未安装 pypdf，跳过（pip install -r requirements.txt 可启用）"
}

$hasOcr = $false
& $py -c "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('rapidocr_onnxruntime') else 1)"
if ($LASTEXITCODE -eq 0) { $hasOcr = $true }

if ($hasOcr -and $env:FINAUDIT_OCR_TEST -eq "1") {
    Write-Host "== 2c/3 扫描件 OCR 路径（RapidOCR，约 2 分钟）=="
    & $py -m finaudit.cli run --fixtures fixtures/demo01_scan --out ../out/demo01_scan | Out-Null
    Write-Host "   扫描件路径完成：../out/demo01_scan"
} else {
    Write-Host "== 2c/3 跳过扫描件路径（需 rapidocr_onnxruntime 且设置 FINAUDIT_OCR_TEST=1）=="
}

Write-Host "== 3/3 交付物 =="
Get-ChildItem -Path ../out/demo01 -Recurse -File | Select-Object -ExpandProperty FullName
Write-Host "O1 报告：../out/demo01/o1/O1-核查报告.md"
Write-Host "演示记录：../out/demo01/demo-transcript.md"
Write-Host "评测报告：../out/demo01/eval-report.md"
Write-Host "审计包：../out/demo01/o3/manifest.json"
