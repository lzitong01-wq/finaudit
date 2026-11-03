"""预设与自检测试：预设解析、未知预设报错、mock 自检可用。"""

from __future__ import annotations

import unittest

from finaudit.doctor import probe
from finaudit.llm import LLMClient, LLMConfig, LLMError
from finaudit.presets import PRESETS, describe, egress_of, resolve


class TestPresets(unittest.TestCase):
    def test_known_presets(self) -> None:
        for name in ("mock", "modelscope", "deepseek"):
            cfg = resolve(name)
            self.assertIn("base_url", cfg)
            self.assertTrue(cfg["model"])

    def test_unknown_preset_raises(self) -> None:
        with self.assertRaises(KeyError):
            resolve("nope")

    def test_override_model(self) -> None:
        cfg = resolve("modelscope", model="Qwen/Qwen3.5-397B-A17B")
        self.assertEqual(cfg["model"], "Qwen/Qwen3.5-397B-A17B")
        self.assertEqual(cfg["base_url"], PRESETS["modelscope"]["base_url"])

    def test_doctor_mock(self) -> None:
        ok, message = probe("mock")
        self.assertTrue(ok)
        self.assertIn("mock", message)


class TestEgressGate(unittest.TestCase):
    """外部模型端点未授权不得发送材料（数据安全）。"""

    def test_egress_classification(self) -> None:
        self.assertEqual(egress_of(""), "none")
        self.assertEqual(egress_of("http://127.0.0.1:11434/v1"), "local")
        self.assertEqual(egress_of("http://localhost:8000/v1"), "local")
        self.assertEqual(egress_of("https://api.deepseek.com/v1"), "external")

    def test_preset_egress(self) -> None:
        self.assertEqual(resolve("mock")["egress"], "none")
        self.assertEqual(resolve("local")["egress"], "local")
        self.assertEqual(resolve("modelscope")["egress"], "external")
        # 覆盖为本机端点后，外发口径按实际端点重判
        self.assertEqual(resolve("modelscope", base_url="http://127.0.0.1:11434/v1")["egress"],
                         "local")
        self.assertTrue(all("egress" in d for d in describe()))

    def test_external_blocked_without_consent(self) -> None:
        cfg = LLMConfig(provider="openai", base_url="https://api.deepseek.com/v1",
                        model="deepseek-chat", allow_external=False)
        with self.assertRaises(LLMError) as ctx:
            LLMClient(cfg).call("result_explain.v1", {"record_json": "{}"})
        self.assertIn("授权", str(ctx.exception))

    def test_local_endpoint_allowed_without_consent(self) -> None:
        """本机端点不属于外发：未授权也应放行到调用阶段（此处只验证不被外发门禁拦截）。"""
        cfg = LLMConfig(provider="openai", base_url="http://127.0.0.1:1/v1",
                        model="local", allow_external=False, timeout=1)
        with self.assertRaises(LLMError) as ctx:
            LLMClient(cfg).call("result_explain.v1", {"record_json": "{}"})
        self.assertNotIn("授权", str(ctx.exception))

    def test_doctor_blocks_external_without_consent(self) -> None:
        ok, message = probe("deepseek")
        self.assertFalse(ok)
        self.assertIn("授权", message)


if __name__ == "__main__":
    unittest.main()
