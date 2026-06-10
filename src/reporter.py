"""测试结果报告生成"""

from datetime import datetime
from pathlib import Path

OUTPUT_DIR = Path("output")


def _has_suite_meta(results: list[dict]) -> bool:
    """判断结果中是否包含 suite 元数据"""
    return any(r.get("suite_meta") or r.get("tc_ids") for r in results)


def _extract_cache_stats(results: list[dict]) -> dict:
    """提取缓存统计信息"""
    for r in results:
        if r.get("cache_stats"):
            return r["cache_stats"]
    return {}


def generate_report(results: list[dict]) -> str:
    """生成文本报告，返回报告路径"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = OUTPUT_DIR / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"report_{timestamp}.txt"

    total = len(results)
    passed = sum(1 for r in results if r["status"] == "passed")
    failed = sum(1 for r in results if r["status"] == "failed")
    skipped = sum(1 for r in results if r["status"] == "skipped")

    lines = [
        "=" * 60,
        f"  测试报告  {timestamp}",
        "=" * 60,
        f"  总计: {total}  通过: {passed}  失败: {failed}  跳过: {skipped}",
        "-" * 60,
    ]

    # 添加缓存统计（如果有）
    cache_stats = _extract_cache_stats(results)
    if cache_stats:
        lines.append("  🚀 Locator 缓存统计:")
        lines.append(f"     缓存条目: {cache_stats.get('total_entries', 0)}")
        lines.append(f"     使用条目: {cache_stats.get('used_entries', 0)}")
        lines.append(f"     命中率: {cache_stats.get('hit_rate', 0):.1%}")
        lines.append("-" * 60)

    if _has_suite_meta(results):
        lines.extend(_generate_suite_detail(results))
    else:
        lines.extend(_generate_simple_detail(results))

    # 生成功能用例 TC-ID 汇总
    tc_summary = _generate_tc_summary(results)
    if tc_summary:
        lines.append("=" * 60)
        lines.append("  功能用例 (TC-ID) 测试结果汇总")
        lines.append("-" * 60)
        lines.extend(tc_summary)

    lines.append("=" * 60)

    text = "\n".join(lines)
    report_path.write_text(text, encoding="utf-8")
    print(text)
    return str(report_path)


def _generate_simple_detail(results: list[dict]) -> list[str]:
    """生成简单模式的详情"""
    lines = []
    for i, r in enumerate(results, 1):
        icon = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}.get(r["status"], "????")
        lines.append(f"  [{icon}] {r['name']}")
        if r.get("error"):
            lines.append(f"        错误: {r['error']}")
        if r.get("screenshot_dir"):
            lines.append(f"        截图: {r['screenshot_dir']}")
        lines.append("")
    return lines


def _generate_suite_detail(results: list[dict]) -> list[str]:
    """生成套件模式的详情，包含 TC-ID 映射"""
    lines = []
    for i, r in enumerate(results, 1):
        status = r["status"]
        icon = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}.get(status, "????")
        name = r["name"]
        tc_ids = r.get("tc_ids", [])
        depends_on = r.get("depends_on", [])

        # 场景名称行
        detail_line = f"  [{icon}] {name}"
        if depends_on:
            detail_line += f"  (依赖: {', '.join(depends_on)})"
        lines.append(detail_line)

        # TC-ID 映射
        if tc_ids:
            tc_status = "✅" if status == "passed" else ("❌" if status == "failed" else "⏭️")
            lines.append(f"        功能用例: {', '.join(tc_ids)}  {tc_status}")

        if r.get("error"):
            lines.append(f"        错误: {r['error']}")
        if r.get("screenshot_dir"):
            lines.append(f"        截图: {r['screenshot_dir']}")
        lines.append("")

    return lines


def _generate_tc_summary(results: list[dict]) -> list[str]:
    """生成功能用例 TC-ID 维度的汇总

    将每个 TC-ID 映射到对应的测试结果。
    同一个 TC-ID 可能出现在多个场景中，取最差结果。
    """
    tc_results = {}  # tc_id -> {status, scenarios}
    status_order = {"failed": 0, "skipped": 1, "passed": 2}
    status_label = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}

    for r in results:
        tc_ids = r.get("tc_ids", [])
        if not tc_ids:
            continue
        for tc_id in tc_ids:
            if tc_id not in tc_results:
                tc_results[tc_id] = {"status": r["status"], "scenarios": []}
            # 取最差状态
            if status_order.get(r["status"], 3) < status_order.get(tc_results[tc_id]["status"], 3):
                tc_results[tc_id]["status"] = r["status"]
            tc_results[tc_id]["scenarios"].append(r["name"])

    if not tc_results:
        return []

    lines = []
    # 按状态排序：失败在前
    sorted_tcs = sorted(tc_results.items(), key=lambda x: status_order.get(x[1]["status"], 3))

    for tc_id, info in sorted_tcs:
        icon = status_label.get(info["status"], "????")
        scenarios = ", ".join(dict.fromkeys(info["scenarios"]))  # 去重保序
        lines.append(f"  [{icon}] {tc_id}  <- {scenarios}")

    # 统计
    tc_total = len(tc_results)
    tc_passed = sum(1 for info in tc_results.values() if info["status"] == "passed")
    tc_failed = sum(1 for info in tc_results.values() if info["status"] == "failed")
    tc_skipped = sum(1 for info in tc_results.values() if info["status"] == "skipped")
    lines.append("")
    lines.append(f"  功能用例总计: {tc_total}  通过: {tc_passed}  失败: {tc_failed}  跳过: {tc_skipped}")

    return lines
