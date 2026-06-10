"""加载 YAML/JSON 测试用例"""

import json
import os
import re
from pathlib import Path
from typing import Union

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env(text: str) -> str:
    """将文本中的 ${VAR} 替换为环境变量值，避免把凭证等敏感信息硬编码进用例。"""

    def repl(match: "re.Match[str]") -> str:
        name = match.group(1)
        val = os.getenv(name)
        if val is None:
            raise ValueError(f"用例引用了未设置的环境变量: {name}（请在 .env 中配置）")
        return val

    return _ENV_PATTERN.sub(repl, text)


REQUIRED_FIELDS = {"name", "steps"}
VALID_ACTIONS = {"navigate", "input", "click", "assert", "wait", "scroll", "select"}

# 外部格式 type -> 内部 action 的映射
TYPE_ACTION_MAP = {
    "browser": "navigate",
    "input": "input",
    "smartTapFunction": "click",
    "click": "click",
    "wait": "wait",
    "assert": "assert",
    "scroll": "scroll",
    "select": "select",
}


def load_case(filepath: Union[str, Path]) -> dict:
    """加载单个测试用例文件，返回结构化 dict"""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"用例文件不存在: {path}")

    text = _expand_env(path.read_text(encoding="utf-8"))
    raw = _parse(text, path.suffix)
    case = _normalize(raw, path)
    _validate(case)
    case["_source"] = str(path)
    return case


def load_suite(filepath: Union[str, Path]) -> dict:
    """加载 suite.yaml 套件文件，返回套件配置 dict

    返回结构：
    {
        "name": "套件名称",
        "base_url": "...",
        "login": {"enabled": bool, "url": "...", "steps": [...]},
        "cases": [
            {"file": "s1-xxx.yaml", "name": "S1: ...", "depends_on": [], "tc_ids": [...]},
            ...
        ],
        "resolved_cases": [  # 加载后的完整用例列表
            {"name": "...", "steps": [...], "tc_ids": [...], "_suite_meta": {...}},
            ...
        ],
        "skipped": []  # 因依赖失败而跳过的场景编号
    }
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"套件文件不存在: {path}")

    text = _expand_env(path.read_text(encoding="utf-8"))
    suite = yaml.safe_load(text)

    if not isinstance(suite, dict):
        raise ValueError("suite.yaml 格式错误：顶层必须是 dict")

    if "cases" not in suite:
        raise ValueError("suite.yaml 缺少 cases 字段")

    suite_dir = path.parent

    # 加载每个用例文件并注入元数据
    resolved = []
    for case_ref in suite["cases"]:
        case_file = case_ref["file"]
        case_path = suite_dir / case_file
        case = load_case(case_path)

        # 从 suite 继承 base_url（用例自身没设置时）
        if "base_url" not in case and suite.get("base_url"):
            case["base_url"] = suite["base_url"]

        # 注入 suite 元数据（用于报告生成）
        case["_suite_meta"] = {
            "suite_name": suite.get("name", ""),
            "depends_on": case_ref.get("depends_on", []),
            "tc_ids": case_ref.get("tc_ids", []),
            "case_ref_name": case_ref.get("name", case["name"]),
        }
        # 也把 tc_ids 放到顶层方便访问
        if case_ref.get("tc_ids") and "tc_ids" not in case:
            case["tc_ids"] = case_ref["tc_ids"]

        resolved.append(case)

    suite["resolved_cases"] = resolved
    suite["_suite_dir"] = str(suite_dir)
    return suite


def is_suite_file(filepath: Union[str, Path]) -> bool:
    """判断文件是否为 suite.yaml（包含 cases 字段的 YAML）"""
    path = Path(filepath)
    if not path.exists():
        return False
    if path.suffix not in (".yaml", ".yml"):
        return False
    # 文件名包含 suite 视为套件文件，或者内容包含 cases 字段
    if "suite" in path.stem.lower():
        return True
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        return isinstance(data, dict) and "cases" in data
    except Exception:
        return False


def load_directory(dirpath: Union[str, Path]) -> list:
    """加载目录下所有 .yaml/.yml/.json 用例"""
    path = Path(dirpath)
    if not path.is_dir():
        raise NotADirectoryError(f"不是目录: {path}")

    # 优先检查是否有 suite.yaml
    suite_files = [f for f in path.glob("suite.*") if f.suffix in (".yaml", ".yml")]
    if suite_files:
        suite = load_suite(suite_files[0])
        return suite["resolved_cases"]

    files = sorted(path.glob("*.yaml")) + sorted(path.glob("*.yml")) + sorted(path.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"目录下没有用例文件: {path}")

    return [load_case(f) for f in files]


def _parse(text: str, suffix: str):
    if suffix in (".yaml", ".yml"):
        return yaml.safe_load(text)
    if suffix == ".json":
        return json.loads(text)
    raise ValueError(f"不支持的文件格式: {suffix}，请使用 .yaml/.yml/.json")


def _normalize(raw, path: Path) -> dict:
    """将各种格式统一为 {name, steps: [{action, ...}]} 结构"""
    # 格式1: 标准 dict 格式 {name, steps: [{action, ...}]}
    if isinstance(raw, dict):
        return raw

    # 格式2: 纯数组 [{step, target, type, input?}, ...]
    if isinstance(raw, list) and len(raw) > 0 and isinstance(raw[0], dict) and "type" in raw[0]:
        steps = []
        for item in raw:
            action = TYPE_ACTION_MAP.get(item["type"])
            if action is None:
                raise ValueError(f"未知的 type '{item['type']}'，支持: {list(TYPE_ACTION_MAP.keys())}")

            step = {"action": action}
            step["description"] = item.get("step", "")

            if action == "navigate":
                step["url"] = item.get("target", "")
            elif action == "input":
                step["value"] = item.get("input", "")
                step["target"] = item.get("target", "")
            elif action == "click":
                step["target"] = item.get("target", "")
            elif action in ("wait", "assert"):
                step["target"] = item.get("target", "")

            steps.append(step)

        return {
            "name": path.stem,
            "steps": steps,
        }

    raise ValueError("用例格式错误：顶层必须是 dict 或 step 数组")


def _validate(case: dict):
    if not isinstance(case, dict):
        raise ValueError("用例格式错误：顶层必须是 dict")

    missing = REQUIRED_FIELDS - set(case.keys())
    if missing:
        raise ValueError(f"用例缺少必填字段: {missing}")

    steps = case["steps"]
    if not isinstance(steps, list) or len(steps) == 0:
        raise ValueError("steps 必须是非空列表")

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"step {i} 必须是 dict")
        action = step.get("action")
        if action not in VALID_ACTIONS:
            raise ValueError(f"step {i} 的 action '{action}' 无效，可选: {VALID_ACTIONS}")
