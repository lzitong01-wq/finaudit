# 研鉴（FinAudit）

[![tests](https://github.com/lzitong01-wq/finaudit/actions/workflows/tests.yml/badge.svg)](https://github.com/lzitong01-wq/finaudit/actions/workflows/tests.yml)

研报主张逐条核验引擎。输入一份研报及其授权证据材料（年报、公告、报表），输出每条主张的
核验状态、判定理由、命中规则编号，以及可复算的审计包。

LLM 只做一件事：把研报正文拆成逐条主张。判定与计算全部由确定性规则引擎（53 条规则）
和公式引擎（16 条公式）执行，同一条主张重复运行结果一致。

不能做的（如实声明）：不做估值；不判断披露真实性与会计政策恰当性；表格解析依赖
"一行一条记录"版式；OCR 识别错字会影响判定（低置信分档拦截其中一部分）。

当前版本 1.7.2。

---

## 1. 运行

### 1.1 环境

Python 3.11+。核心判定链路零第三方依赖，只用标准库。
3.11 / 3.13 / 3.14 均已跑通全量测试，CI 按这三个版本跑（矩阵见 `.github/workflows/tests.yml`）。

```bash
cd code
pip install -r requirements.txt      # 可选：pypdf（PDF 文本层）、rapidocr_onnxruntime（扫描件 OCR）
```

不装这两个包时，Markdown 材料路径不受影响。

### 1.2 一键跑通

以下命令均在 `code/` 下执行（`demo.sh` / `demo.ps1` 就在该目录）：

```bash
cd code
bash demo.sh                                        # Git Bash / Linux / macOS
powershell -ExecutionPolicy Bypass -File demo.ps1    # Windows PowerShell
```

依次执行：单元测试 → 端到端核验 → D0–D8 演示记录 → PDF 路径（已装 pypdf 时）→ 输出清单。
扫描件 OCR 路径默认跳过（单次约 2 分钟），需显式启用：

```bash
FINAUDIT_OCR_TEST=1 bash demo.sh
```

图形界面双击根目录的 `启动研鉴.bat` 即可。

### 1.3 命令行

以下命令均在 `code/` 下执行：

| 命令 | 作用 |
|---|---|
| `python -m finaudit.cli run --fixtures fixtures/demo01 --out ../out/demo01` | 跑完整 S0–S7 流水线，产出 O1/O2/O3 |
| `python -m finaudit.cli demo --fixtures fixtures/demo01 --out ../out/demo01` | 同上并额外写 D0–D8 演示记录 |
| `python -m finaudit.cli roster` | 打印 RA1–RA8 的读/写/禁边界 |
| `python -m finaudit.cli verify --out ../out/demo01` | 按 manifest 复算输出文件哈希，不一致返回非零 |
| `python -m finaudit.cli doctor --preset modelscope` | 模型连通性自检（把 401/402/403/404/429 翻译成结论） |
| `python -m unittest discover -s tests` | 全量测试（OCR 3 项受 `FINAUDIT_OCR_TEST` 门控） |
| `python -m finaudit.cli --version` | 打印版本号（与 README、B-versions.json 同源） |

内置夹具四组：`demo01`（Markdown）、`demo01_pdf`（PDF 文本层）、`demo01_scan`（扫描件）、
`bench`（300 条规模基准）。

退出码：`0` 完成；`1` 校验不通过（`verify` 哈希不符、`doctor` 探活失败）；
`2` 输入不合契约或运行中止。后两类只打印一行结论，不抛回溯。

### 1.4 本地界面

```bash
python webui.py                       # 默认 http://127.0.0.1:8765，自动开浏览器
python webui.py --port 9000 --no-browser
```

两种用法：

1. **上传材料**：选研报 + 证据材料（PDF/MD/TXT，可多选）→ 指定哪份是研报 → 填受理参数
   （主体化名、期间、数据截止点）→ 点"上传并核验"。上传材料没有金标准，必须用
   `local`（本机模型）或 `modelscope` / `deepseek` 由模型拆解主张。
2. **用内置夹具**：选 demo01 / demo01_pdf / demo01_scan / bench → 点"运行核验"。

按钮行的「将运行：…」始终显示本次实际目标；改选夹具会清空上传目标，避免点了运行却跑错对象。

页面四区：

| 区 | 内容 |
|---|---|
| A 任务概览 | 上传/选夹具、受理参数、状态分布、运行配置 |
| B Agent 流程 | 启动即展示 RA1–RA8 职责；运行后更新状态、S0–S7 阶段、交接物与最近事件 |
| C 主张与证据 | 逐条主张 + 候选证据，点「定位原文」回看材料块原文 |
| D 复核与交付 | 计算过程、审计事件、人工复核留痕（只留痕不改状态）、O1/O2/O3 下载 |

界面只用标准库实现，零前端依赖；渲染走 DOM 文本节点，不用 innerHTML 拼接内容。
API Key 在页面填写，只存在该作业的内存配置里，不写入环境变量、文件或日志。

### 1.5 接真实模型

```bash
export FINAUDIT_LLM_API_KEY=***
python -m finaudit.cli doctor --preset modelscope
python -m finaudit.cli run --fixtures fixtures/demo01 --out ../out/demo01_llm --llm-preset modelscope
```

预设：`mock`（离线占位）、`local`（本机模型，材料不出本机）、`modelscope`、`deepseek`。
后两者是外部端点，命令行需加 `--allow-external-llm`，界面需勾选「确认外发」；未授权时
不发送研报正文，也不静默降级。自定义端点用 `--llm-provider openai --llm-base-url <url>
--llm-model <name>`，外发口径按实际端点判定。

---

## 2. 输入与输出

**输入**（一个夹具目录）：

| 文件 | 内容 |
|---|---|
| `materials/` | 研报正文 + 授权证据材料 |
| `materials.json` | 材料登记与授权范围；`publish_date`（可选）为披露日，早于它的数据截止点会把该材料整份排除出证据 |
| `task_order.json` | 任务单（主体化名、期间、数据截止点） |
| `claims_gt.json` | 金标准主张清单，仅离线评测使用 |

两份登记文件在入口就过契约校验（`finaudit/inputs.py`），字段写错不会被静默忽略：

| 文件 | 必填 | 可选 |
|---|---|---|
| `task_order.json` | `task_id`、`subject_alias`、`period_scope`、`data_cutoff` | `contract_version`、`materials`、`boundary_statement`、`missing_items` |
| `materials[]` 每项 | `material_id`、`source_file_ref`、`role` | `format`（`md`/`pdf`/`scanned_pdf`）、`page_count`、`publish_date`、`ocr_blocks` |

多写或拼错字段会直接报错并指出像哪个合法字段（`publishdate` → 是不是想写
`publish_date`），不再是 `KeyError` 回溯、更不是静默失效。`data_cutoff` 是必填：
没有它，「这条主张当时能不能核」无从判断，前视偏差过滤也无从生效。

`source_file_ref` 指向的文件不存在时，研报缺失直接中止；**证据材料缺失不中止**，
该材料登记为「无效」并在 O1 运行配置表的「材料可用性」一行点名，
`input_formats` 只统计成功解析的材料，S9 的放行结论同时注明「结论仅覆盖已读到的材料」。

**输出**（`out/<任务名>/`）：

| 文件 | 内容 |
|---|---|
| `o1/O1-核查报告.md` | 人读报告：运行配置、结论摘要、逐项核验表、类型分区、冲突/证据不足专区、计算附录、边界说明、检查与放行 |
| `o2/O2-核查明细.json` / `.csv` | 机读明细（CSV 带 BOM，Excel 直接打开） |
| `o3/A-inputs.json` | 材料哈希 + 块级原文索引（界面的「定位原文」按此回看） |
| `o3/B-versions.json` | 规则版本、公式版本、工具版本、运行配置 |
| `o3/C-events.jsonl` | 全量审计事件流 |
| `o3/D-calculations.json` | 确定性计算记录（输入、公式、输出、容差、差值） |
| `o3/E-reviews.json` | 导出时已存在的人工复核记录（复核在导出之后进行，故常为空；重跑时把保留的 `review-log.jsonl` 一并收进来，无法解析的行只计数不中断导出） |
| `o3/review-log.jsonl` | 界面人工复核留痕，运行后追加；无人复核时不生成 |
| `o3/F-outputs.json` | 输出文件哈希与扫描结果 |
| `o3/G-reproduce.md` | 复现说明（按实际运行配置生成） |
| `o3/manifest.json` | 以上文件的清单与校验值 |
| `eval-report.md` | 评测指标（双口径）与样本量局限 |
| `demo-transcript.md` | D0–D8 演示记录（仅 `demo` 模式生成） |

输出目录是**本次运行的快照**：重跑到同一目录会先清掉 o1/o2/o3 里上一次的产物
（`o3/review-log.jsonl` 是跨运行累积的复核留痕，保留），清理条数记在 S0 事件里。
`verify` 除了复算哈希，也会报出包里未登记在清单上的文件——哈希全对但混进旧文件，
同样不算「包一致」。

---

## 3. 判定口径

**八值状态**：待核验 / 通过 / 错误 / 冲突 / 证据不足 / 低置信 / 需人工复核 / 不适用。
状态只有唯一写点，由规则引擎按固定顺序判定，越权的判定路径会被拦截并留痕。

判定顺序（冲突与计算失败前置）：

1. 定位缺失 → 待核验
2. 冲突证据前置检查 → 冲突
3. 计算失败 → 需人工复核
4. 会计解释类（收入确认、会计政策/估计变更、重述等）→ 需人工复核
5. 估值假设类 → 需人工复核
6. 无候选证据 → 证据不足
7. 观点 / 未来期间推论 → 不适用
8. 定性事实字面匹配 → 通过 / 需人工复核
9. 引用型核验
10. 数值型：单位与币种 → 期间一致性 → 实体 → 口径 → 比较 → 约数/区间 → 勾稽 → 公式输入齐备
11. 勾稽不平的责任分流：研报侧不平 → 错误；证据侧不平 → 冲突

**规则与公式**：53 条规则（R-ACC / R-ASM / R-CIT / R-CMP / R-CON / R-ENT / R-FRM / R-GEN /
R-NUM / R-PER / R-TIE / R-UNI 十二域）全部实现；16 条公式（CMP 比较类 5 条、IND 指标类
7 条、TIE 勾稽类 4 条）。容差必须有来源（公式缺省值或最粗输入的展示粒度），不允许统一给一个数。

公式库 16 条，主张路由当前接 8 条（同比、较年初、毛利率、净利率、资产负债率、流动比率、
期末净资产收益率、资产负债表基本等式）。其余 8 条在 `pipeline.py` 的 `UNROUTED_FORMULAS`
里逐条写明为什么没接（缺分季期间、缺应收账款行、缺分项明细等），测试强制每条公式都要表态，
既不接也不说明的写法会失败。

**确定性**：编号分配只取决于输入处理顺序，不依赖时钟与随机数；计算用十进制，中间不舍入；
时间戳允许存在但不参与比对。同一输入重复运行，状态签名一致。

**导出前检查**：密钥扫描、化名扫描各零命中，契约校验（每项主张的 Claim / VerificationResult /
Calculation 逐个校验）全部通过，才判「放行」；任一不合格即「阻断」，明细写进 O1 的 S9 一节。

---

## 4. 目录结构

```
README.md                    本文件
LICENSE                      MIT 许可证
启动研鉴.bat                  Windows 图形界面启动
.github/workflows/tests.yml   CI：3.11 / 3.13 / 3.14 三档跑测试与端到端
code/
  finaudit/                  23 个模块（见第 5 节）
  webui.py                   界面服务与接口
  web_page.py                页面模板（单文件 HTML + 内联 CSS/JS）
  fixtures/                  4 组夹具：demo01 / demo01_pdf / demo01_scan / bench
  tests/                     22 个测试文件（项数以命令输出为准）
  tools/                     夹具生成器 + 三套浏览器探针
  demo.sh / demo.ps1         一键复现脚本
  requirements.txt           可选依赖
out/                         运行输出（生成物，已被 .gitignore 排除，不进仓库）
```

`tools/` 内容：

| 脚本 | 用途 |
|---|---|
| `gen_benchmark.py` | 生成 300 条规模基准夹具 |
| `md_to_pdf.py` | 把 Markdown 夹具转成 PDF 文本层夹具 |
| `md_to_scanned_pdf.py` | 生成扫描件 PDF 夹具（验证 OCR 路径） |
| `probe_ui.js` | 界面交互自检（上传、运行、定位原文、复核留痕） |
| `probe_layout.js` | 响应式自检（多宽度截图） |
| `probe_resume.js` | 刷新后状态恢复自检 |

三个 `.js` 探针依赖 playwright-core，运行前设 `NODE_PATH` 指向其 `node_modules`。

---

## 5. 模块与接口

| 模块 | 职责 | 关键接口 |
|---|---|---|
| `ids.py` | 编号分配 | `IdAllocator.next(kind) -> str` |
| `numeric.py` | 数值、单位换算与容差 | `parse_granularity()`、`unit_scale()`、`to_unit()`、`tolerance_abs()`、`within()` |
| `periods.py` | 期间解析与规范化 | `parse_period_label()`、`period_key()`、`period_for_metric()`、同比/期末基期推导 |
| `formulas.py` | 16 条公式 | `FORMULAS`、`compute(formula_id, inputs, params=None) -> dict`，失败抛 `FormulaError` |
| `rules.py` | 规则引擎 | `evaluate(claim, ctx) -> dict{status, rule_ids, reason, suggestion, severity}` |
| `contract.py` | 轻量结构校验 | `validate(obj, object_name) -> list[str]` |
| `inputs.py` | 夹具输入契约（任务单与材料登记字段） | `check_task()`、`check_materials()`、`check_all()` |
| `events.py` | 审计事件 | `EventLog(task_id, alloc, sink=None).emit(...) -> dict` |
| `agents.py` | Agent 框架 | `ROLES`/`REGIONS`、`assert_read`/`assert_write`、`HANDOFFS`/`verify_handoff`、`Supervisor` |
| `pipeline.py` | S0–S7 编排 | `run(fixtures_dir, out_dir, llm_config=None, on_event=None) -> dict` |
| `report.py` | O1/O2/O3 生成 | `write_o1()`、`write_o2()`、`write_o3()` |
| `extract.py` | 主张抽取 | 长报告分块（6,000 字符 + 300 重叠）与逐条规范化 |
| `pdfparse.py` | PDF 文本层解析 | 需 pypdf |
| `ocr.py` | 扫描件 OCR | 需 rapidocr_onnxruntime；置信度分档（≥0.90 高 / ≥0.75 中 / <0.75 低） |
| `llm.py` | 模型接入 | 提示模板版本化、调用留痕（EV-05）、失败重试一次后抛错 |
| `presets.py` | 模型预设与外发口径 | `PRESETS`、`resolve()`、`egress_of()` |
| `alias.py` | 化名映射 | 白名单单一来源 |
| `scan.py` | 导出前扫描 | 密钥特征、非白名单主体名 |
| `verify.py` | 交付包校验 | `verify_output(out) -> dict`，按 manifest 复算哈希 |
| `eval.py` | 评测报告 | 双口径指标与样本量局限 |
| `demo.py` | 演示脚本 | D0–D8 九步记录 |
| `doctor.py` | 模型自检 | 网关错误翻译成结论 |
| `cli.py` | 命令行 | `run` / `demo` / `roster` / `verify` / `doctor` |

模块之间只传 dict，键名与 `contract.py` 里的数据契约一致。状态值只允许八值，事件类型只允许
19 值，事件 payload 的键名不得包含 secret / token / password / credential / api_key / private_key。

---

## 6. 依赖与许可

| 依赖 | 许可证 | 用途 | 是否必需 |
|---|---|---|---|
| `pypdf` | BSD-3-Clause | PDF 文本层解析 | 可选 |
| `rapidocr_onnxruntime` | Apache-2.0 | 扫描件 OCR（含 onnxruntime 等传递依赖） | 可选 |
| `reportlab` | BSD | 仅生成测试夹具，运行时不需要 | 开发时 |

核心判定链路只用 Python 标准库。`onnxruntime` 等传递依赖的许可证随发行包附带。

本项目本体以 MIT 许可证发布，全文见 `LICENSE`。上述第三方依赖各自保留其原始许可证。

---

## 7. 测试与自检

```
全量测试        全通过（OCR 3 项需 FINAUDIT_OCR_TEST=1）
端到端核验      demo01 20 条主张，八值分布齐全，隔离重跑状态签名一致
契约校验        20/20 项通过（Claim / VerificationResult / Calculation 逐项真实校验，
                不合格项会写进 S9 并阻断放行）
PDF 路径        pypdf 6.21.4，demo01_pdf 端到端通过
扫描件路径      RapidOCR，OCR 端到端通过（实测约 2 分钟）
长报告抽取      覆盖 6,000 字符分块与重叠边界
期间口径        年度/季度/半年/时点/区间及相对表头映射，比较列不沿用当前期间
失败路径        真实模型失败不回退夹具金标准；模型 0 条主张时不静默补答
```

除了单元测试，还有两类自检可随时跑：

- `python -m finaudit.cli verify --out <输出目录>`：按 `o3/manifest.json` 复算每个文件的
  SHA-256，返回非零表示交付包被改动过。
- `code/tools/probe_*.js`：浏览器探针，覆盖上传、运行、定位原文、刷新恢复与多宽度布局。

---

## 8. 已知限制

1. 检索是确定性文本/数值匹配，没有向量检索；换了措辞的同一事实可能匹配不到。
2. 主张路由只覆盖 8 条公式：估值类、以及变动量表述（"毛利率同比下降 2 个百分点"）
   都不在其中，统一转人工复核，不套近似公式充当结论。
