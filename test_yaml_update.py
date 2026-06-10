#!/usr/bin/env python3
"""测试 YAML 自动更新功能

走真实自愈路径：extract → apply_healing_to_case → update_yaml_file，
并断言用例 value 确实被改写（而非只写 _healing_metadata）。
"""

import os
import yaml

from src.learning import apply_healing_to_case, update_yaml_file

# 原始用例（value=立即执行 是会被自愈的字段）
test_case = {
    "name": "测试用例",
    "steps": [
        {"action": "select", "target": "执行类型", "value": "立即执行"},
        {"action": "input", "target": "店铺ID", "value": "1001"},
    ],
}

# 落一个原始 YAML 文件
test_yaml = "test_healing.yaml"
with open(test_yaml, "w", encoding="utf-8") as f:
    yaml.dump(test_case, f, allow_unicode=True, sort_keys=False)

print("原 YAML 内容:")
with open(test_yaml, "r", encoding="utf-8") as f:
    print(f.read())

# 模拟 Agent 上报的自愈动作
healing_actions = [
    {"original": "立即执行", "actual": "处罚", "location": "执行类型下拉框", "reason": "页面选项与用例不符"}
]

# 关键：先把自愈应用到用例，再写回（这才是 learn_from_result 的真实流程）
healed_case = apply_healing_to_case(test_case, healing_actions)
updated = update_yaml_file(test_yaml, healed_case, healing_actions)

assert updated, "update_yaml_file 应返回 True"

with open(test_yaml, "r", encoding="utf-8") as f:
    result = yaml.safe_load(f)

print("\n更新后 YAML 内容:")
with open(test_yaml, "r", encoding="utf-8") as f:
    print(f.read())

# 断言：value 真的从「立即执行」改成了「处罚」，且保留了原始值
step0 = result["steps"][0]
assert step0["value"] == "处罚", f"value 应被自愈为「处罚」，实际为「{step0['value']}」"
assert step0.get("_metadata", {}).get("original_value") == "立即执行", "应保留 original_value"
assert result["steps"][1]["value"] == "1001", "未自愈的字段不应被改动"

# 清理
os.remove(test_yaml)
print("\n✅ 测试通过：value 已自愈为「处罚」并保留原始值")
