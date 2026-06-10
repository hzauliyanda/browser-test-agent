"""Locator 缓存管理

缓存成功步骤的 selector，回归时优先用缓存定位，失败才走 Agent。
目标：回归测试 LLM 调用 ↓70%，速度 ↑2-3 倍。
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, Any
from playwright.async_api import Page, Locator


class LocatorCache:
    """Locator 缓存管理器"""

    def __init__(self, cache_file: str = ".locator-cache.json"):
        self.cache_file = Path(cache_file)
        self.cache: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self):
        """从文件加载缓存"""
        if self.cache_file.exists():
            try:
                self.cache = json.loads(self.cache_file.read_text(encoding="utf-8"))
            except:
                self.cache = {}

    def _save(self):
        """保存缓存到文件"""
        self.cache_file.write_text(
            json.dumps(self.cache, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def make_key(self, case_name: str, step_index: int, action: str, target: str) -> str:
        """生成缓存键

        格式: {case_name}_{step_index}_{action}_{target_hash}
        例如: S1-处罚任务创建_3_click_新增处罚
        """
        import hashlib
        target_hash = hashlib.md5(target.encode()).hexdigest()[:8]
        return f"{case_name}_{step_index}_{action}_{target_hash}"

    def get(self, case_name: str, step_index: int, action: str, target: str) -> Optional[Dict[str, Any]]:
        """获取缓存的 selector"""
        key = self.make_key(case_name, step_index, action, target)
        return self.cache.get(key)

    def set(self, case_name: str, step_index: int, action: str, target: str,
             selector: str, strategy: str = "css", metadata: Optional[Dict] = None):
        """设置缓存

        Args:
            case_name: 用例名称
            step_index: 步骤索引（从1开始）
            action: 动作类型（click/input/select等）
            target: 目标描述
            selector: CSS selector 或 XPath
            strategy: 定位策略（css/xpath/text/role）
            metadata: 额外元数据（如 index、状态等）
        """
        key = self.make_key(case_name, step_index, action, target)
        self.cache[key] = {
            "selector": selector,
            "strategy": strategy,
            "target": target,
            "action": action,
            "created_at": datetime.now().isoformat(),
            "last_used": datetime.now().isoformat(),
            "use_count": 0,
            "metadata": metadata or {}
        }
        self._save()

    def hit(self, case_name: str, step_index: int, action: str, target: str):
        """记录缓存命中"""
        key = self.make_key(case_name, step_index, action, target)
        if key in self.cache:
            self.cache[key]["last_used"] = datetime.now().isoformat()
            self.cache[key]["use_count"] += 1
            self._save()

    def miss(self, case_name: str, step_index: int, action: str, target: str):
        """记录缓存未命中（用于分析）"""
        key = self.make_key(case_name, step_index, action, target)
        if key in self.cache:
            # 缓存存在但定位失败，标记为失效
            self.cache[key]["status"] = "failed"
            self.cache[key]["last_used"] = datetime.now().isoformat()

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        total = len(self.cache)
        used = sum(1 for v in self.cache.values() if v.get("use_count", 0) > 0)
        failed = sum(1 for v in self.cache.values() if v.get("status") == "failed")
        return {
            "total_entries": total,
            "used_entries": used,
            "failed_entries": failed,
            "hit_rate": used / total if total > 0 else 0
        }

    def clear_failed(self):
        """清理失效的缓存条目"""
        to_remove = [k for k, v in self.cache.items() if v.get("status") == "failed"]
        for k in to_remove:
            del self.cache[k]
        if to_remove:
            self._save()
        return len(to_remove)


# 全局单例
_global_cache: Optional[LocatorCache] = None


def get_cache() -> LocatorCache:
    """获取全局缓存实例"""
    global _global_cache
    if _global_cache is None:
        _global_cache = LocatorCache()
    return _global_cache


async def try_cached_locator(page: Page, cached: Dict[str, Any]) -> Optional[Locator]:
    """尝试使用缓存的 locator 定位元素

    Returns:
        定位到的 Locator，如果定位失败返回 None
    """
    selector = cached.get("selector")
    strategy = cached.get("strategy", "css")

    try:
        if strategy == "css":
            locator = page.locator(selector)
        elif strategy == "xpath":
            locator = page.locator(f"xpath={selector}")
        elif strategy == "text":
            locator = page.get_by_text(selector)
        elif strategy == "role":
            # 解析 role 配置，格式如 "button[name='登录']"
            locator = page.get_by_role(cached["metadata"].get("role", "button"))
        else:
            locator = page.locator(selector)

        # 尝试访问第一个元素来验证定位是否成功
        await locator.first.wait_for(timeout=3000, state="visible")
        return locator

    except Exception:
        # 定位失败，返回 None 让调用方走 Agent
        return None
