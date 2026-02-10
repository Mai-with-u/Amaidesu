"""Golden Dataset 测试运行器"""

import json
from pathlib import Path


# 测试数据路径
GOLDEN_DATASETS_DIR = Path(__file__).parent / "golden_datasets"


def test_intent_parser_dataset_exists():
    """测试 intent_parser.jsonl 存在"""
    assert (GOLDEN_DATASETS_DIR / "intent_parser.jsonl").exists()


def test_local_llm_dataset_exists():
    """测试 local_llm.jsonl 存在"""
    assert (GOLDEN_DATASETS_DIR / "local_llm.jsonl").exists()


def test_vts_hotkey_dataset_exists():
    """测试 vts_hotkey.jsonl 存在"""
    assert (GOLDEN_DATASETS_DIR / "vts_hotkey.jsonl").exists()


def test_intent_parser_dataset_format():
    """测试 intent_parser.jsonl 格式正确"""
    with open(GOLDEN_DATASETS_DIR / "intent_parser.jsonl", "r", encoding="utf-8") as f:
        for _line_num, line in enumerate(f, 1):
            if line.strip():
                data = json.loads(line)
                assert "input" in data
                assert "expected_emotion" in data


def test_local_llm_dataset_format():
    """测试 local_llm.jsonl 格式正确"""
    with open(GOLDEN_DATASETS_DIR / "local_llm.jsonl", "r", encoding="utf-8") as f:
        for _line_num, line in enumerate(f, 1):
            if line.strip():
                data = json.loads(line)
                assert "input" in data
                assert "expected_length" in data
                assert "expected_style" in data


def test_vts_hotkey_dataset_format():
    """测试 vts_hotkey.jsonl 格式正确"""
    with open(GOLDEN_DATASETS_DIR / "vts_hotkey.jsonl", "r", encoding="utf-8") as f:
        for _line_num, line in enumerate(f, 1):
            if line.strip():
                data = json.loads(line)
                assert "input" in data
                assert "hotkey_list" in data
                assert "expected_output" in data
