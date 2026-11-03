"""LLM 接入层与主张拆解测试：提示版本、mock 输出、EV-05 留痕、失败降级。"""

from __future__ import annotations

import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from finaudit.events import EventLog
from finaudit.extract import _parse_numeric, _strip_fences, extract_claims
from finaudit.ids import IdAllocator
from finaudit.llm import LLMClient, LLMConfig, LLMError, prompt_hash

BASE = Path(__file__).resolve().parents[1]
FIX = BASE / "fixtures" / "demo01"


def _mock_payload() -> Path:
    gt = json.loads((FIX / "claims_gt.json").read_text(encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp()) / "mock_claims.json"
    tmp.write_text(json.dumps([
        {"claim_text": e["claim"]["claim_text"],
         "page": (e["claim"].get("report_locator") or {}).get("page")
                 if e["claim"].get("report_locator") else None,
         "claim_type": e["claim"]["claim_type"],
         "judgment_type": e["claim"]["judgment_type"],
         "numeric": e["claim"].get("numeric"),
         "reference": e["claim"].get("reference")} for e in gt],
        ensure_ascii=False, default=str), encoding="utf-8")
    return tmp


class TestLLMLayer(unittest.TestCase):
    def test_prompt_hash_stable(self) -> None:
        self.assertEqual(prompt_hash("claim_extract.v1"), prompt_hash("claim_extract.v1"))
        self.assertEqual(len(prompt_hash("claim_extract.v1")), 16)

    def test_unknown_prompt_raises(self) -> None:
        client = LLMClient(LLMConfig())
        with self.assertRaises(LLMError):
            client.call("not.exist.v1", {})

    def test_openai_without_base_url_raises(self) -> None:
        client = LLMClient(LLMConfig(provider="openai", model="m"))
        with self.assertRaises(LLMError):
            client.call("claim_extract.v1", {"report_text": "x"})

    def test_api_key_is_config_scoped_and_hidden_from_repr(self) -> None:
        secret = "test-secret-key-123"

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"{}"}}]}'

        cfg = LLMConfig(provider="openai", model="m", base_url="http://localhost/v1",
                        api_key=secret)
        with patch("urllib.request.urlopen", return_value=Response()) as urlopen:
            LLMClient(cfg).call("result_explain.v1", {"record_json": "{}"})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), f"Bearer {secret}")
        self.assertNotIn(secret, repr(cfg))

    def test_numeric_period_uses_task_scope_and_actual_material(self) -> None:
        scope = {"kind": "期间", "start": "2024-07-01", "end": "2024-09-30",
                 "point": None, "label": "2024Q3"}
        numeric = _parse_numeric("甲企业营业收入为10亿元", default_period=scope,
                                 material_id="MAT-0009")
        self.assertEqual(numeric["period"]["start"], "2024-07-01")
        self.assertEqual(numeric["source"]["material_id"], "MAT-0009")
        point = _parse_numeric("甲企业期末资产合计为20亿元", default_period=scope)
        self.assertEqual(point["period"]["kind"], "时点")
        self.assertEqual(point["period"]["point"], "2024-09-30")
        unknown = _parse_numeric("甲企业营业收入为10亿元")
        self.assertEqual(unknown["period"]["label"], "期间未确定")

    def test_extract_covers_long_report_and_ignores_untrusted_numeric(self) -> None:
        claim_text = "甲企业2025年度营业收入为12亿元"

        class StubLLM:
            config = SimpleNamespace(provider="openai")

            def __init__(self):
                self.calls = []

            def call(self, prompt_id, variables):
                chunk = variables["report_text"]
                self.calls.append(chunk)
                items = []
                if claim_text in chunk:
                    items = [{"claim_text": claim_text, "page": 2,
                              "claim_type": "数值型", "judgment_type": "事实",
                              "numeric": {"value": "999", "value_text": "999亿元",
                                          "unit": {"code": "万元"}}}]
                return {"text": json.dumps(items, ensure_ascii=False)}

        llm = StubLLM()
        report = "背景内容。" * 1300 + "\n" + claim_text + "。"
        claims = extract_claims(report, llm, IdAllocator(), "TSK-0001", "MAT-0009")
        self.assertGreater(len(llm.calls), 1)
        self.assertEqual(len(claims), 1)
        self.assertEqual(str(claims[0]["numeric"]["value"]), "12")
        self.assertEqual(claims[0]["numeric"]["unit"]["code"], "亿元")
        self.assertEqual(claims[0]["numeric"]["period"]["start"], "2025-01-01")
        self.assertEqual(claims[0]["numeric"]["source"]["material_id"], "MAT-0009")

    def test_mock_extract_20_claims(self) -> None:
        events = EventLog("TSK-0001", IdAllocator())
        client = LLMClient(LLMConfig(mock_claims_path=str(_mock_payload())), events)
        text = (FIX / "materials" / "MAT-0001-研报.md").read_text(encoding="utf-8")
        claims = extract_claims(text, client, IdAllocator(), "TSK-0001", "MAT-0001")
        self.assertEqual(len(claims), 20)
        self.assertEqual(claims[0]["claim_id"], "CLM-0001")
        self.assertIsNotNone(claims[0]["report_locator"])
        self.assertIsNone(claims[16]["report_locator"])  # CLM-0017 定位缺失
        ev_types = [e["event_type"] for e in events.events]
        self.assertEqual(ev_types.count("LLM调用"), 1)
        payload = events.events[0]["payload"]
        for key in ("model", "model_version", "prompt_version", "input_summary",
                    "output_summary", "retry_count"):
            self.assertIn(key, payload)

    def test_extract_failure_returns_empty(self) -> None:
        client = LLMClient(LLMConfig(provider="openai", model="m", base_url=""))
        claims = extract_claims("text", client, IdAllocator(), "TSK-0001", "MAT-0001")
        self.assertEqual(claims, [])

    def test_fixture_numeric_carried(self) -> None:
        client = LLMClient(LLMConfig(mock_claims_path=str(_mock_payload())))
        text = (FIX / "materials" / "MAT-0001-研报.md").read_text(encoding="utf-8")
        claims = extract_claims(text, client, IdAllocator(), "TSK-0001", "MAT-0001")
        c1 = claims[0]
        self.assertEqual(str(c1["numeric"]["value"]), "1285.0")
        self.assertEqual(c1["numeric"]["unit"]["code"], "亿元")

    def test_strip_fences(self) -> None:
        raw = '以下是抽取结果：\n```json\n[{"claim_text": "x"}]\n```\n共 1 条。'
        self.assertEqual(_strip_fences(raw), '[{"claim_text": "x"}]')
        self.assertEqual(_strip_fences('[{"a":1}]'), '[{"a":1}]')
        self.assertEqual(_strip_fences('说明\n[{"a":1}]\n结尾'), '[{"a":1}]')


if __name__ == "__main__":
    unittest.main()
