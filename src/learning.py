"""自愈学习：记录自愈动作，更新 YAML 用例"""

import re
import json
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime


def extract_healing_actions(agent_result: str) -> List[Dict[str, str]]:
    """从 Agent 最终结果中提取自愈动作

    Args:
        agent_result: Agent 的最终结果文本

    Returns:
        自愈动作列表，每项包含原始值、实际值、位置、原因
    """
    actions = []

    # 格式1: 结构化格式
    # 自愈记录：
    # - 原始值: XXX
    #   实际值: YYY
    if '自愈记录' in agent_result:
        lines = agent_result.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i]
            if '原始值:' in line:
                original = line.split('原始值:')[1].strip()
                # 查找后续的实际值、位置、原因
                actual = location = reason = '未知'
                j = i + 1
                while j < len(lines) and lines[j].startswith('  '):
                    if '实际值:' in lines[j]:
                        actual = lines[j].split('实际值:')[1].strip()
                    elif '位置:' in lines[j]:
                        location = lines[j].split('位置:')[1].strip()
                    elif '原因:' in lines[j]:
                        reason = lines[j].split('原因:')[1].strip()
                    j += 1

                actions.append({
                    'original': original,
                    'actual': actual,
                    'location': location,
                    'reason': reason
                })
            i += 1

    # 结构化格式（format1）是 prompt 强制要求的输出，解析可靠。
    # 只有在 format1 完全没解析到时，才退回自然语言正则兜底——
    # 否则正则会在 Agent 的叙述性文字里误抓出「但」「本」这类单字噪声。
    if actions:
        return actions

    # 格式2: 自然语言描述
    # "实际选择的是X而不是Y"
    patterns = [
        r'实际选择(?:的|是)[「\s]*([^「\s]+?)[？」\s]*，?而(?:不是|非)[「\s]*([^「\s]+?)[？」\s]*',
        r'实际(?:选择|使用|输入)[：\s]*([^，\n]+?)，?(?:但|而)?用例(?:要求|写)[：\s]*([^，\n]+?)',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, agent_result)
        for actual, original in matches:
            if not _is_valid_healing(original, actual):
                continue
            # 避免重复
            if not any(a.get('original') == original.strip() for a in actions):
                actions.append({
                    'original': original.strip(),
                    'actual': actual.strip(),
                    'location': '未指定',
                    'reason': '页面选项与用例不符'
                })

    # 格式3: 从错误/验证部分提取
    # "用例要求X，但页面实际显示Y"
    pattern3 = r'用例要求[「\s]*([^「\n]+?)[？」\s]*，.*?(?:页面实际|实际)?(?:显示为|显示|是|为)?[：「\s]*([^「\n]+?)[？」\s]*'
    matches3 = re.findall(pattern3, agent_result)

    for original, actual in matches3:
        if not _is_valid_healing(original, actual):
            continue
        # 避免重复
        if not any(a['original'] == original.strip() for a in actions):
            actions.append({
                'original': original.strip(),
                'actual': actual.strip(),
                'location': '未指定',
                'reason': '用例与实际不符'
            })

    return actions


# 正则兜底常把连接词/单字误判成自愈值，这里过滤掉明显的噪声
_HEALING_STOPWORDS = {'但', '而', '本', '是', '为', '的', '了', '在', '和', '与', '页面', '实际', '用例', '则', '其'}


def _is_valid_healing(original: str, actual: str) -> bool:
    """判断一对 (original, actual) 是否是有效的自愈记录

    过滤掉自然语言正则常见的误判：单字、连接词、首尾相同、空值。
    """
    o = (original or '').strip().strip('「」“”"\'')
    a = (actual or '').strip().strip('「」“”"\'')

    if not o or not a:
        return False
    # 单字几乎都是正则把连接词抓进来的噪声
    if len(o) < 2 or len(a) < 2:
        return False
    if o in _HEALING_STOPWORDS or a in _HEALING_STOPWORDS:
        return False
    # 原值与实际值相同，没有自愈发生
    if o == a:
        return False
    return True


def _norm(s) -> str:
    """归一化字符串用于匹配

    关键：Agent 上报的 original 里换行常是字面量「\\n」，而 YAML 里是真实换行符，
    直接 == 会匹配不上（实测主体信息自愈因此没写回）。统一把字面量 \\n 转成真实换行再比。
    """
    return str(s if s is not None else '').replace('\\n', '\n').strip()


def find_step_to_update(case: Dict, healing_action: Dict) -> Optional[int]:
    """找到需要更新的步骤索引

    根据自愈动作中的原始值/位置，找到对应的步骤
    """
    steps = case.get('steps', [])
    original = _norm(healing_action.get('original', ''))
    location = healing_action.get('location', '')

    for i, step in enumerate(steps):
        # 检查 value 字段（归一化换行后比较）
        if _norm(step.get('value')) == original:
            return i

        # 检查 target/description 字段
        if _norm(step.get('target')) == original or _norm(step.get('description')) == original:
            return i

        # 模糊匹配位置信息
        if location and location != '未指定':
            step_desc = step.get('description', '') + step.get('target', '')
            if any(kw in step_desc for kw in [location, location.split(' ')[0]]):
                return i

    return None


def apply_healing_to_case(case: Dict, healing_actions: List[Dict]) -> Dict:
    """将自愈动作应用到测试用例

    Returns:
        更新后的测试用例副本
    """
    import copy
    updated_case = copy.deepcopy(case)

    for action in healing_actions:
        original = action.get('original', '')
        actual = action.get('actual', '')

        if not original or not actual:
            continue

        step_index = find_step_to_update(updated_case, action)
        if step_index is not None:
            step = updated_case['steps'][step_index]
            norm_original = _norm(original)

            # 更新 value（归一化换行后比较，避免字面量 \n 匹配失败）
            if 'value' in step and _norm(step['value']) == norm_original:
                step['value'] = actual
                # 添加元数据记录原始值
                if '_metadata' not in step:
                    step['_metadata'] = {}
                step['_metadata']['original_value'] = original
                step['_metadata']['healed_at'] = datetime.now().isoformat()

            # 更新 target 或 description
            elif 'target' in step and _norm(step['target']) == norm_original:
                step['target'] = actual
            elif 'description' in step and _norm(step['description']) == norm_original:
                step['description'] = actual

    return updated_case


def save_healing_report(case_path: str, healing_actions: List[Dict], result: Dict):
    """保存自愈学习报告

    Args:
        case_path: 原始用例文件路径
        healing_actions: 自愈动作列表
        result: 测试结果
    """
    if not healing_actions:
        return

    report_dir = Path("output/healing-reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    case_name = Path(case_path).stem
    report_path = report_dir / f"{case_name}_healing_{timestamp}.json"

    report = {
        'case_path': case_path,
        'case_name': case_name,
        'timestamp': timestamp,
        'test_status': result.get('status'),
        'healing_actions': healing_actions,
        'summary': {
            'total_actions': len(healing_actions),
            'healed_fields': list(set(a.get('location', 'unknown') for a in healing_actions))
        }
    }

    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return str(report_path)


# 运行时由 loader/suite 注入、不属于源文件的字段，绝不能写回 YAML
_RUNTIME_FIELDS = {'_source', '_suite_meta', 'tc_ids', '_metadata_runtime'}


def _apply_actions_to_steps(steps, healing_actions: List[Dict]) -> int:
    """在（ruamel 的）steps 列表上原地应用自愈动作，返回实际改动条数"""
    applied = 0
    for action in healing_actions:
        original = action.get('original', '')
        actual = action.get('actual', '')
        if not original or not actual:
            continue
        norm_original = _norm(original)
        for step in steps:
            if 'value' in step and _norm(step.get('value')) == norm_original:
                step['value'] = actual
                if '_metadata' not in step:
                    step['_metadata'] = {}
                step['_metadata']['original_value'] = original
                step['_metadata']['healed_at'] = datetime.now().isoformat()
                applied += 1
                break
            if 'target' in step and _norm(step.get('target')) == norm_original:
                step['target'] = actual
                applied += 1
                break
            if 'description' in step and _norm(step.get('description')) == norm_original:
                step['description'] = actual
                applied += 1
                break
    return applied


def update_yaml_file(yaml_path: str, updated_case: Dict, healing_actions: List[Dict]) -> bool:
    """把自愈动作写回 YAML 源文件（原地修改，保留注释与格式）

    用 ruamel.yaml 轮转模式加载，**只就地改命中步骤的 value/target/description**，
    其余部分（注释、引号、缩进）原样保留；运行时注入字段（_source/_suite_meta 等）
    因为从不写入、自然不会污染源文件。

    Args:
        yaml_path: YAML 文件路径
        updated_case: 运行时用例（仅用于取 name 定位套件中的目标用例）
        healing_actions: 自愈动作列表

    Returns:
        是否实际写回了改动
    """
    try:
        from ruamel.yaml import YAML

        yaml_file = Path(yaml_path)
        if not yaml_file.exists():
            print(f"  ⚠️  YAML 文件不存在: {yaml_path}")
            return False

        yaml_rt = YAML()
        yaml_rt.preserve_quotes = True
        yaml_rt.width = 4096  # 避免长行被折行
        with open(yaml_file, 'r', encoding='utf-8') as f:
            data = yaml_rt.load(f)

        if data is None:
            return False

        # 定位要改的 steps：套件文件按 name 匹配，单用例文件直接用顶层 steps
        case_name = updated_case.get('name', '')
        steps = None
        if 'cases' in data:
            for case in data['cases']:
                if case.get('name') == case_name and 'steps' in case:
                    steps = case['steps']
                    break
            # 套件里可能只是 file 引用、没内联 steps，此时无法在本文件改
            if steps is None:
                print(f"  ⚠️  套件文件中未找到内联 steps，跳过写回: {case_name}")
                return False
        elif 'steps' in data:
            steps = data['steps']
        else:
            print(f"  ⚠️  YAML 中无 steps 字段，跳过写回")
            return False

        applied = _apply_actions_to_steps(steps, healing_actions)
        if applied == 0:
            print(f"  ℹ️  无自愈动作匹配到具体步骤，未改动 YAML")
            return False

        # 顶层记录一次自愈元数据
        data['_healing_metadata'] = {
            'last_healed': datetime.now().isoformat(),
            'healing_count': applied,
        }

        with open(yaml_file, 'w', encoding='utf-8') as f:
            yaml_rt.dump(data, f)

        print(f"  ✅ 已写回 YAML（{applied} 处自愈，保留注释）: {yaml_path}")
        return True

    except Exception as e:
        print(f"  ❌ 更新 YAML 失败: {e}")
        return False


class HealingLearner:
    """自愈学习管理器"""

    def __init__(self, enabled: bool = True, auto_update: bool = False):
        self.enabled = enabled
        self.auto_update = auto_update
        self.reports = []
        self.updated_files = []

    def learn_from_result(self, case: Dict, agent_result: str, test_result: Dict, case_path: str):
        """从测试结果中学习

        如果测试通过且有自愈动作，更新 YAML 并保存报告
        """
        if not self.enabled:
            return None

        # 只在学习成功的测试
        if test_result.get('status') != 'passed':
            return None

        # 提取自愈动作
        healing_actions = extract_healing_actions(agent_result)
        if not healing_actions:
            return None

        print(f"  📝 发现 {len(healing_actions)} 个自愈动作，记录学习...")

        # 应用自愈到用例
        updated_case = apply_healing_to_case(case, healing_actions)

        # 保存学习报告
        report_path = save_healing_report(case_path, healing_actions, test_result)
        self.reports.append(report_path)

        # 自动更新 YAML 文件
        if self.auto_update and case_path:
            updated = update_yaml_file(case_path, updated_case, healing_actions)
            if updated:
                self.updated_files.append(case_path)

        return report_path

    def print_summary(self):
        """打印学习摘要"""
        if not self.reports:
            print("\n📊 自愈学习：无新学习内容")
        else:
            print(f"\n📊 自愈学习：已生成 {len(self.reports)} 个学习报告")
            for report in self.reports:
                print(f"   - {report}")


# 全局学习器实例
_global_learner: Optional[HealingLearner] = None


def get_learner(config: Dict = None) -> HealingLearner:
    """获取全局学习器实例

    Args:
        config: 配置字典，包含 learning 配置
    """
    global _global_learner
    if _global_learner is None:
        if config:
            learning_config = config.get("learning", {})
            _global_learner = HealingLearner(
                enabled=learning_config.get("enabled", True),
                auto_update=learning_config.get("auto_update", False)
            )
        else:
            _global_learner = HealingLearner(enabled=True)
    return _global_learner
