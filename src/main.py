"""CLI 入口"""

import argparse
import asyncio
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .reporter import generate_report
from .runner import run


def load_config(config_path: str = "config.yaml") -> dict:
    path = Path(config_path)
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    return {}


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="AI 浏览器自动化测试 Agent")
    parser.add_argument("target", help="测试用例文件或目录路径")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--llm", choices=["zhipu", "anthropic", "google", "browser-use", "openai-compatible", "openai"], help="覆盖 LLM provider")
    parser.add_argument("--model", help="覆盖模型名")
    parser.add_argument("--headless", action="store_true", help="无头模式运行")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.llm:
        config.setdefault("llm", {})["provider"] = args.llm
    if args.model:
        config.setdefault("llm", {})["model"] = args.model
    if args.headless:
        config.setdefault("browser", {})["headless"] = True

    target_path = Path(args.target)
    print(f"🎯 目标: {target_path}\n")

    results = asyncio.run(run(args.target, config))
    report_path = generate_report(results)

    print(f"\n报告已保存: {report_path}")

    # 打印自愈学习摘要
    if config.get("learning", {}).get("enabled", True):
        from .learning import get_learner
        learner = get_learner(config)
        learner.print_summary()

    failed = sum(1 for r in results if r["status"] == "failed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
