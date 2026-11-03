"""LLM 接入层（受限编排 / 调用留痕 / 安全边界）。

定位：LLM 只负责把研报正文拆成逐条主张，判定与计算一律由确定性规则引擎和
公式引擎执行，LLM 不参与结论、不写计算记录。本模块提供：

- 模型注册：provider + model + version，经环境变量配置（封闭环境可指向内网兼容端点）；
- 提示模板：版本号 + 内容哈希，随调用落账；
- 调用留痕：每次调用写 EV-05（model/model_version/prompt_version/input_summary/
  output_summary/retry_count），进 C 组事件流与 O3 审计包；
- 双 provider：
  - `mock`：确定性本地占位（无网络），用于封闭环境演示与自动化测试；
  - `openai`：OpenAI 兼容端点（base_url + api_key 环境变量），真实模型调用。
- 失败策略：超时/非 2xx 记失败并重试 1 次（失败重试策略），仍失败抛 LLMError，
  由管线降级为「需人工复核」，绝不静默通过。

本模块不引入任何第三方依赖；openai 兼容调用使用 urllib。
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .events import EventLog
from .presets import egress_of

PROMPTS: dict[str, dict] = {
    "claim_extract.v1": {
        "version": "claim_extract.v1",
        "template": (
            "你是金融研报核验助手。从下列研报文本中抽取全部可核验主张，逐条输出 JSON 数组，"
            "每条包含 claim_text、page、claim_type（数值型/引用型/定性表述型）、"
            "judgment_type（事实/计算/推论/观点）。不得臆造原文不存在的主张。\n"
            "研报文本：\n{report_text}\n"
        ),
    },
    "claim_extract.v2": {
        "version": "claim_extract.v2",
        "template": (
            "你是金融研报核验助手。从下列研报文本中抽取全部可核验主张，只输出 JSON 数组，"
            "不要任何解释或推理过程。\n"
            "字段要求：\n"
            "1. claim_text：必须逐字摘录原文表述（含数字与单位），不得改写、不得合并多条；\n"
            "2. page：原文所在物理页码，从文本中的 <!-- page N --> 标记推断，必须是整数；\n"
            "3. claim_type：数值型（含可量化数值）/引用型（转引他处材料）/定性表述型（不含数值的陈述）；\n"
            "4. judgment_type：事实（可回链证据核验）/计算（需换算或公式复算）/"
            "推论（预测或推断）/观点（主观评价，不可核验）。\n"
            "输出示例：[{{\"claim_text\":\"甲企业2024年度营业收入为1,285.0亿元\","
            "\"page\":3,\"claim_type\":\"数值型\",\"judgment_type\":\"事实\"}}]\n"
            "研报文本：\n{report_text}\n"
        ),
    },
    "result_explain.v1": {
        "version": "result_explain.v1",
        "template": (
            "以下是核验引擎对一条研报主张的判定记录。请用一句话向投研人员解释判定依据，"
            "只复述记录中的事实与规则编号，不得新增事实、不得改动状态。\n记录：\n{record_json}\n"
        ),
    },
}



def prompt_hash(prompt_id: str) -> str:
    tpl = PROMPTS[prompt_id]["template"]
    return hashlib.sha256(tpl.encode("utf-8")).hexdigest()[:16]


class LLMError(RuntimeError):
    """LLM 调用失败（超时/非 2xx/输出不可解析）。"""


@dataclass
class LLMConfig:
    provider: str = "mock"          # mock | openai
    model: str = "mock-claim-extractor"
    model_version: str = "0.1.0"
    base_url: str = ""              # 例：http://127.0.0.1:8000/v1
    api_key_env: str = "FINAUDIT_LLM_API_KEY"
    api_key: str = field(default="", repr=False, compare=False)
    temperature: float = 0.0
    max_retries: int = 1
    max_tokens: int = 4096          # 推理型模型会把预算花在 reasoning 上，留足
    timeout: int = 180              # 推理型模型单次调用可达 1-2 分钟
    extra_body: dict = field(default_factory=dict)  # 关闭思考等厂商参数（如 thinking/enable_thinking）
    mock_claims_path: str = ""      # mock provider 的结构化输出来源（JSON）
    allow_external: bool = False    # 是否授权把材料正文发往外部端点（默认否，需显式授权）


class LLMClient:
    def __init__(self, config: LLMConfig, events: EventLog | None = None):
        self.config = config
        self.events = events

    # ------------------------------------------------------------------
    def call(self, prompt_id: str, variables: dict) -> dict:
        if prompt_id not in PROMPTS:
            raise LLMError(f"未知提示模板: {prompt_id}")
        prompt = PROMPTS[prompt_id]
        rendered = prompt["template"].format(**variables)
        retries = 0
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                text = self._invoke(prompt_id, rendered)
                self._log(prompt_id, rendered, text, attempt)
                return {"text": text, "prompt_version": prompt["version"],
                        "prompt_hash": prompt_hash(prompt_id),
                        "model": self.config.model,
                        "model_version": self.config.model_version,
                        "retry_count": attempt}
            except Exception as exc:  # noqa: BLE001 - 统一降级
                last_error = exc
                retries = attempt  # 已发生的重试次数 = 当前尝试序号
        self._log(prompt_id, rendered, None, retries, error=str(last_error))
        raise LLMError(str(last_error)) from last_error

    # ------------------------------------------------------------------
    def _invoke(self, prompt_id: str, rendered: str) -> str:
        if self.config.provider == "mock":
            return self._mock(prompt_id, rendered)
        if self.config.provider == "openai":
            return self._openai(rendered)
        raise LLMError(f"未知 provider: {self.config.provider}")

    def _mock(self, prompt_id: str, rendered: str) -> str:
        """确定性本地占位：从提示中的结构化线索还原输出，不联网。

        - claim_extract：读取 mock_claims_path 指向的（夹具）主张表，返回 JSON 数组；
        - result_explain：返回固定模板句（含判定状态），仅供管线演示。
        """
        if prompt_id.startswith("claim_extract."):
            if self.config.mock_claims_path and os.path.exists(self.config.mock_claims_path):
                with open(self.config.mock_claims_path, encoding="utf-8") as fh:
                    return fh.read()
            raise LLMError("mock provider 缺少 mock_claims_path")
        if prompt_id == "result_explain.v1":
            return json.dumps({"explain": "按规则记录复述：见 reason 与 rule_ids。"},
                              ensure_ascii=False)
        raise LLMError(f"mock provider 不支持: {prompt_id}")

    def _openai(self, rendered: str) -> str:
        if not self.config.base_url:
            raise LLMError("openai provider 未配置 base_url")
        egress = egress_of(self.config.base_url)
        if egress == "external" and not self.config.allow_external:
            # 未授权不外发：明确报错，绝不静默改用 mock 或跳过（拒绝臆造、不静默跳过）
            raise LLMError(
                f"端点 {self.config.base_url} 为外部服务，材料正文将离开本机。"
                "需显式授权后才能调用：界面勾选「确认外发」或命令行加 --allow-external-llm；"
                "改用本机模型可选 preset=local。")
        if egress == "external":
            self._log_egress(self.config.base_url, len(rendered))
        # Web 作业把密钥放在单作业配置对象中，避免并发请求争用进程级环境变量。
        # 命令行仍可通过既有环境变量注入；密钥不进入 repr、调用日志或输出产物。
        api_key = self.config.api_key or os.environ.get(self.config.api_key_env, "")
        body = json.dumps({
            "model": self.config.model,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "messages": [{"role": "user", "content": rendered}],
            **self.config.extra_body,
        }).encode("utf-8")
        req = urllib.request.Request(
            self.config.base_url.rstrip("/") + "/chat/completions",
            data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:  # noqa: S310
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 把服务端错误正文透出（如 Insufficient Balance / 余额不足），便于定位
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "ignore")[:300]
            except Exception:  # noqa: BLE001
                pass
            raise LLMError(f"HTTP {exc.code}：{detail or exc.reason}") from exc
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError("响应结构不符合 OpenAI 兼容格式") from exc

    # ------------------------------------------------------------------
    def _log_egress(self, base_url: str, size: int) -> None:
        """外发前留痕（EV 事件类型「外发请求」）：只记端点与规模，不记正文。"""
        if self.events is None:
            return
        self.events.emit("外发请求", "S4", "Agent", "RA4",
                         {"端点": base_url, "授权": "已确认",
                          "发送内容": "研报正文（主张拆解提示）", "字符数": size})

    def _log(self, prompt_id: str, rendered: str, text: str | None,
             retries: int, error: str | None = None) -> None:
        if self.events is None:
            return
        payload = {
            "model": self.config.model,
            "model_version": self.config.model_version,
            "prompt_version": PROMPTS[prompt_id]["version"],
            "prompt_hash": prompt_hash(prompt_id),
            "input_summary": rendered[:120],
            "output_summary": (text or "")[:120],
            "retry_count": retries,
            "provider": self.config.provider,
        }
        if error:
            payload["failure_kind"] = "调用失败"
            payload["error_summary"] = error[:160]
        self.events.emit("LLM调用", "S4", "Agent", "RA4", payload)
