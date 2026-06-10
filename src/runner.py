"""Browser Use 执行引擎"""

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from browser_use import Agent, Browser
from playwright.async_api import Page, Locator

from .loader import is_suite_file, load_case, load_directory, load_suite
from .cache import get_cache, try_cached_locator
from .learning import get_learner

OUTPUT_DIR = Path("output")


async def _execute_step_with_cache(
    page: Page,
    step: dict,
    step_index: int,
    case_name: str,
    cache_enabled: bool = True
) -> bool:
    """执行单个步骤，支持 selector 缓存

    Args:
        page: Playwright Page 对象
        step: 步骤配置
        step_index: 步骤索引（从1开始）
        case_name: 用例名称
        cache_enabled: 是否启用缓存

    Returns:
        True 表示步骤成功，False 表示需要走 Agent
    """
    if not cache_enabled:
        return False

    selector_hint = step.get("selector")
    if not selector_hint:
        return False

    cache = get_cache()
    action = step["action"]
    target = step.get("target", "")

    # 尝试从缓存获取
    cached = cache.get(case_name, step_index, action, target)
    if cached:
        locator = await try_cached_locator(page, cached)
        if locator:
            # 缓存命中，执行动作
            try:
                await _execute_action(locator, action, step)
                cache.hit(case_name, step_index, action, target)
                print(f"    [缓存命中] {action} → {target}")
                return True
            except Exception:
                # 执行失败，标记缓存失效
                cache.miss(case_name, step_index, action, target)
                print(f"    [缓存失效] {action} → {target}")
                return False

    # 尝试使用 YAML 中的 selector hint
    try:
        locator = page.locator(selector_hint)
        await locator.first.wait_for(timeout=3000, state="visible")

        # 执行动作
        await _execute_action(locator, action, step)

        # 成功后记录到缓存
        cache.set(case_name, step_index, action, target, selector_hint)
        print(f"    [selector直接定位] {action} → {target}")
        return True

    except Exception:
        # 定位失败，走 Agent
        return False


async def _execute_action(locator: Locator, action: str, step: dict):
    """执行具体动作"""
    if action == "click":
        await locator.click()
    elif action == "input":
        value = step.get("value", "")
        await locator.fill(value)
    elif action == "select":
        value = step.get("value", "")
        # select 需要先点开下拉框，再选选项
        await locator.click()
        # 等待选项出现并选择
        option_locator = locator.page.get_by_text(value, exact=True)
        await option_locator.click()
    elif action == "wait":
        seconds = step.get("seconds", 2)
        await locator.page.wait_for_timeout(seconds * 1000)
    elif action == "assert":
        # 验证元素可见
        await locator.expect("to_be_visible")


async def _try_playwright_execution(page: Page, case: dict, cache_enabled: bool = True) -> Optional[dict]:
    """尝试用纯 Playwright 执行（不调 LLM）

    如果所有步骤都有 selector hint，可以用 Playwright 直接执行，速度更快。

    Returns:
        执行成功返回结果，无法执行返回 None
    """
    steps = case.get("steps", [])
    case_name = case["name"].replace(" ", "_")

    # 检查是否所有步骤都有 selector
    for i, step in enumerate(steps, 1):
        if not step.get("selector"):
            return None  # 有步骤没有 selector，无法纯 Playwright 执行

    cache = get_cache()
    result = {
        "status": "passed",
        "steps_with_cache": 0,
        "steps_total": len(steps)
    }

    print(f"  [快速模式] 所有步骤有 selector，尝试纯 Playwright 执行...")

    for i, step in enumerate(steps, 1):
        action = step["action"]
        target = step.get("target", "")
        selector = step.get("selector")

        # 检查缓存
        cached = cache.get(case_name, i, action, target)

        try:
            if cached:
                # 尝试用缓存的 selector
                locator = await try_cached_locator(page, cached)
                if locator:
                    await _execute_action(locator, action, step)
                    cache.hit(case_name, i, action, target)
                    result["steps_with_cache"] += 1
                    continue
                else:
                    cache.miss(case_name, i, action, target)

            # 用 YAML 中的 selector
            locator = page.locator(selector)
            await locator.first.wait_for(timeout=3000, state="visible")
            await _execute_action(locator, action, step)

            # 记录到缓存
            cache.set(case_name, i, action, target, selector)

            # 等待页面稳定
            await page.wait_for_timeout(500)

        except Exception as e:
            # 执行失败，返回 None 让 Agent 接手
            print(f"  [快速模式] 步骤 {i} 失败: {e}，切换到 Agent...")
            return None

    result["status"] = "passed"
    return result


def _build_task_prompt(case: dict) -> str:
    """将测试用例转成 Browser Use 的自然语言 task"""
    lines = [f"执行以下测试用例：{case['name']}"]
    base_url = case.get("base_url", "")

    for i, step in enumerate(case["steps"], 1):
        action = step["action"]
        desc = step.get("description", "")
        target = step.get("target", "")

        if action == "navigate":
            url = step.get("url", target)
            if base_url and not url.startswith("http"):
                url = base_url.rstrip("/") + "/" + url.lstrip("/")
            lines.append(f"{i}. 打开页面 {url}")

        elif action == "input":
            value = step.get("value", "")
            loc = target or desc
            lines.append(f"{i}. 在「{loc}」中输入「{value}」")

        elif action == "click":
            loc = target or desc
            lines.append(f"{i}. 点击「{loc}」")

        elif action == "assert":
            lines.append(f"{i}. 验证：{desc or target}")

        elif action == "wait":
            if target or desc:
                lines.append(f"{i}. 等待直到：{desc or target}")
            else:
                seconds = step.get("seconds", 2)
                lines.append(f"{i}. 等待 {seconds} 秒")

        elif action == "scroll":
            direction = step.get("direction", "down")
            lines.append(f"{i}. 向{direction}滚动页面")

        elif action == "select":
            value = step.get("value", "")
            lines.append(f"{i}. 在「{desc}」中选择「{value}」")

    # 加入数据自愈指令
    lines.append("\n【重要：数据自愈策略】")
    lines.append("如果执行过程中遇到以下情况，必须进行数据自愈：")
    lines.append("- 数据不存在（如店铺ID、商品ID、任务ID等）")
    lines.append("- 无权限访问该数据")
    lines.append("- 数据状态不可用（已删除、已冻结等）")
    lines.append("- 接口返回错误（如400/403/404/500）")
    lines.append("")
    lines.append("数据自愈步骤：")
    lines.append("1. 返回列表页（或当前模块的列表页）")
    lines.append("2. 从列表中查找其他可用的数据（优先选择最近创建的）")
    lines.append("3. 如果列表数据不足，点击进入详情页验证数据是否真正可用")
    lines.append("4. 用找到的可用数据替换原数据继续执行")
    lines.append("5. 在最终报告中记录使用了自愈数据")
    lines.append("")
    lines.append("注意：不要因为数据问题而放弃测试，尽可能用自愈方式完成。")
    lines.append("")
    lines.append("【最终报告要求】")
    lines.append("测试完成后，必须在最终结果报告中列出所有自愈动作，格式：")
    lines.append("自愈记录：")
    lines.append("- 原始值: {原始值}")
    lines.append("  实际值: {自愈后的值}")
    lines.append("  位置: {字段/步骤描述}")
    lines.append("  原因: {为什么需要自愈}")

    return "\n".join(lines)


def _build_login_prompt(login_config: dict) -> str:
    """将登录配置转成 Browser Use 的自然语言 task"""
    url = login_config.get("url", "")
    lines = [f"执行登录操作"]

    if url:
        lines.append(f"1. 打开登录页面 {url}")

    offset = 2 if url else 1
    for i, step in enumerate(login_config.get("steps", [])):
        action = step["action"]
        desc = step.get("description", "")
        target = step.get("target", "")

        if action == "input":
            value = step.get("value", "")
            loc = target or desc
            lines.append(f"{offset}. 在「{loc}」中输入「{value}」")
        elif action == "click":
            loc = target or desc
            lines.append(f"{offset}. 点击「{loc}」")
        elif action == "assert":
            lines.append(f"{offset}. 验证：{desc or target}")
        elif action == "wait":
            lines.append(f"{offset}. 等待直到：{desc or target}")

        offset += 1

    return "\n".join(lines)


def _get_llm(config: dict):
    """根据配置创建 LLM 实例"""
    llm_config = config.get("llm", {})
    provider = llm_config.get("provider", "zhipu")
    model = llm_config.get("model")
    api_key = os.getenv("LLM_API_KEY") or os.getenv("ZHIPU_API_KEY") or llm_config.get("token", "")
    api_base = llm_config.get("api_base") or llm_config.get("base_url")
    temperature = llm_config.get("temperature")

    if provider == "zhipu":
        from browser_use.llm.litellm.chat import ChatLiteLLM
        return ChatLiteLLM(
            model=model or "openai/glm-5.1",
            api_key=api_key,
            api_base=api_base or "https://open.bigmodel.cn/api/paas/v4",
            temperature=temperature,
        )

    if provider == "anthropic":
        from browser_use import ChatAnthropic
        return ChatAnthropic(model=model or "claude-sonnet-4-6")

    if provider == "google":
        from browser_use import ChatGoogle
        return ChatGoogle(model=model or "gemini-2.5-flash")

    if provider == "browser-use":
        from browser_use import ChatBrowserUse
        return ChatBrowserUse()

    if provider == "openai-compatible":
        from browser_use.llm.litellm.chat import ChatLiteLLM
        return ChatLiteLLM(
            model=model,
            api_key=api_key,
            api_base=api_base,
            temperature=temperature,
        )

    if provider == "openai":
        from browser_use.llm.litellm.chat import ChatLiteLLM
        model_name = model or "qwen3.5"
        # litellm 需要 openai/ 前缀走 OpenAI-compatible 路径
        if not model_name.startswith("openai/"):
            model_name = f"openai/{model_name}"
        return ChatLiteLLM(
            model=model_name,
            api_key=api_key or "qwen3.5",
            api_base=api_base,
            temperature=temperature,
        )

    raise ValueError(f"不支持的 LLM provider: {provider}")


def _make_step_screenshot_callback(browser: Browser, screenshot_dir: Path):
    """创建每步截图回调"""
    async def callback(browser_state, agent_output, step_number):
        path = str(screenshot_dir / f"step_{step_number:03d}.png")
        try:
            await browser.take_screenshot(path=path)
        except Exception:
            pass
    return callback


def _check_agent_result(history) -> tuple:
    """检查 Agent 执行结果，返回 (成功?, 错误信息)

    agent.run() 不抛异常不代表成功，需要检查 history.is_successful()。
    """
    if history is None:
        return False, "Agent 返回空结果"

    successful = history.is_successful()
    if successful is True:
        return True, None

    # 提取错误信息
    final = history.final_result()
    errors = []
    if history.history:
        for step in history.history:
            if step.result:
                for r in step.result:
                    if r.error:
                        errors.append(r.error)
    error_parts = []
    if final:
        error_parts.append(str(final))
    if errors:
        error_parts.append("步骤错误: " + "; ".join(errors[-3:]))

    msg = " | ".join(error_parts) if error_parts else "Agent 判定任务未成功完成"
    return False, msg


async def run_case(case: dict, config: dict, browser: Optional[Browser] = None) -> dict:
    """执行单个测试用例，返回结果

    如果传入 browser，则复用该浏览器实例（用于 suite 模式）。
    否则创建新的浏览器实例。
    """
    cache_config = config.get("cache", {})
    cache_enabled = cache_config.get("enabled", False)

    browser_config = config.get("browser", {})
    headless = browser_config.get("headless", False)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    case_name = case["name"].replace(" ", "_")
    video_dir = OUTPUT_DIR / "videos"
    screenshot_dir = OUTPUT_DIR / "screenshots" / f"{case_name}_{timestamp}"
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    suite_meta = case.get("_suite_meta", {})
    tc_ids = case.get("tc_ids", suite_meta.get("tc_ids", []))
    depends_on = suite_meta.get("depends_on", [])

    result = {
        "name": case["name"],
        "source": case.get("_source", ""),
        "status": "pending",
        "error": None,
        "video_path": None,
        "screenshot_dir": str(screenshot_dir),
        "timestamp": timestamp,
        "tc_ids": tc_ids,
        "depends_on": depends_on,
        "suite_meta": suite_meta,
        "cache_stats": None,
    }

    # Agent 模式：走常规流程
    task_prompt = _build_task_prompt(case)
    llm = _get_llm(config)
    max_retries = config.get("retry", {}).get("max_attempts", 1)

    # suite 模式复用浏览器时用 keep_alive，独立模式用自己的浏览器
    own_browser = browser is None

    for attempt in range(1 + max_retries):
        current_browser = browser
        try:
            if current_browser is None:
                current_browser = Browser(headless=headless)
            on_step = _make_step_screenshot_callback(current_browser, screenshot_dir)
            agent = Agent(
                task=task_prompt,
                llm=llm,
                browser=current_browser,
                register_new_step_callback=on_step,
                use_judge=False,
            )
            history = await asyncio.wait_for(agent.run(), timeout=600)

            ok, err_msg = _check_agent_result(history)
            if ok:
                result["status"] = "passed"

                # 自愈学习：提取 Agent 的自愈动作并记录
                if config.get("learning", {}).get("enabled", True):
                    try:
                        agent_final_result = history.final_result() if history else ""
                        if agent_final_result:
                            learner = get_learner(config)
                            case_path = case.get("_source", "")
                            learner.learn_from_result(
                                case=case,
                                agent_result=str(agent_final_result),
                                test_result=result,
                                case_path=case_path
                            )
                    except Exception as e:
                        print(f"  ⚠️  自愈学习记录失败: {e}")

                # 缓存统计
                if cache_enabled:
                    cache = get_cache()
                    result["cache_stats"] = cache.get_stats()
                break
            else:
                result["error"] = err_msg
                if attempt < max_retries:
                    print(f"  重试 ({attempt + 1}/{max_retries})...")
                    continue
                result["status"] = "failed"

        except Exception as e:
            result["error"] = str(e)
            if attempt < max_retries:
                print(f"  重试 ({attempt + 1}/{max_retries})...")
                continue
            result["status"] = "failed"
        finally:
            # 只关闭自己创建的浏览器（独立模式）
            if own_browser and current_browser:
                await current_browser.stop()

    return result


def _extract_scenario_id(case_name: str) -> Optional[str]:
    """从用例名称中提取场景编号（如 S1, S2）"""
    import re
    m = re.match(r"(S\d+)", case_name, re.IGNORECASE)
    return m.group(1).upper() if m else None


async def run_suite(suite: dict, config: dict) -> list[dict]:
    """执行套件模式：登录一次，在同一浏览器中执行所有用例"""
    login_config = suite.get("login", {})
    cases = suite["resolved_cases"]
    llm = _get_llm(config)
    browser_config = config.get("browser", {})
    headless = browser_config.get("headless", False)
    cache_config = config.get("cache", {})
    cache_enabled = cache_config.get("enabled", False)

    results = []
    browser = None
    login_ok = False

    try:
        # keep_alive=True 防止 Agent 完成后自动关闭浏览器
        browser = Browser(headless=headless, keep_alive=True)
        await browser.start()  # 先启动浏览器

        # 步骤1：登录（如果需要）
        if login_config.get("enabled", False):
            print("🔐 执行登录...")
            login_prompt = _build_login_prompt(login_config)
            login_screenshot_dir = OUTPUT_DIR / "screenshots" / "login"
            login_screenshot_dir.mkdir(parents=True, exist_ok=True)

            # 登录可能走 SSO（如 SKYOA Portal）重定向，流程较长；超时可配置
            login_timeout = config.get("timeout", {}).get("login", 240)
            try:
                on_step = _make_step_screenshot_callback(browser, login_screenshot_dir)
                agent = Agent(
                    task=login_prompt,
                    llm=llm,
                    browser=browser,
                    register_new_step_callback=on_step,
                    use_judge=False,
                )
                try:
                    history = await asyncio.wait_for(agent.run(), timeout=login_timeout)
                except asyncio.TimeoutError:
                    raise RuntimeError(f"登录超时（{login_timeout}s），SSO 流程可能未完成")

                ok, err_msg = _check_agent_result(history)
                if not ok:
                    raise RuntimeError(f"登录未成功: {err_msg}")

                login_ok = True
                print("  ✅ 登录成功\n")
            except Exception as e:
                print(f"  ❌ 登录失败: {e}\n")
                # 登录失败，所有用例标记为跳过
                for case in cases:
                    suite_meta = case.get("_suite_meta", {})
                    tc_ids = case.get("tc_ids", suite_meta.get("tc_ids", []))
                    results.append({
                        "name": case["name"],
                        "source": case.get("_source", ""),
                        "status": "skipped",
                        "error": f"登录失败: {e}",
                        "video_path": None,
                        "screenshot_dir": None,
                        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
                        "tc_ids": tc_ids,
                        "depends_on": suite_meta.get("depends_on", []),
                        "suite_meta": suite_meta,
                    })
                return results
        else:
            login_ok = True

        # 步骤2：按顺序执行用例，处理依赖关系
        failed_scenarios = set()  # 已失败的场景编号集合

        # 如果启用缓存，打印提示
        if cache_enabled:
            print("🚀 缓存模式已启用，将优先使用缓存的 selector\n")

        for i, case in enumerate(cases, 1):
            suite_meta = case.get("_suite_meta", {})
            tc_ids = case.get("tc_ids", suite_meta.get("tc_ids", []))
            depends_on = suite_meta.get("depends_on", [])
            scenario_id = _extract_scenario_id(case["name"])

            # 检查依赖是否满足
            skipped_deps = [d for d in depends_on if d in failed_scenarios]
            if skipped_deps:
                print(f"[{i}/{len(cases)}] ⏭️  跳过: {case['name']}（依赖 {', '.join(skipped_deps)} 失败）")
                results.append({
                    "name": case["name"],
                    "source": case.get("_source", ""),
                    "status": "skipped",
                    "error": f"依赖场景失败: {', '.join(skipped_deps)}",
                    "video_path": None,
                    "screenshot_dir": None,
                    "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
                    "tc_ids": tc_ids,
                    "depends_on": depends_on,
                    "suite_meta": suite_meta,
                })
                if scenario_id:
                    failed_scenarios.add(scenario_id)
                continue

            print(f"[{i}/{len(cases)}] 执行: {case['name']}")
            r = await run_case(case, config, browser=browser)
            status_icon = "PASS" if r["status"] == "passed" else "FAIL"
            print(f"  结果: {status_icon}\n")
            results.append(r)

            if r["status"] == "failed" and scenario_id:
                failed_scenarios.add(scenario_id)

    finally:
        if browser:
            await browser.stop()

    return results


async def run(filepath: str, config: dict) -> list[dict]:
    """入口：执行文件或目录"""
    path = Path(filepath)

    # 检测是否为 suite 文件
    if path.is_file() and is_suite_file(path):
        suite = load_suite(path)
        print(f"📋 套件模式: {suite.get('name', '未命名')}")
        login_config = suite.get("login", {})
        if login_config.get("enabled"):
            print(f"🔐 将在执行前登录")
        print(f"📦 共 {len(suite['resolved_cases'])} 个场景\n")
        return await run_suite(suite, config)

    # 普通模式：加载用例
    if path.is_dir():
        # 检查目录中是否有 suite.yaml
        suite_files = [f for f in path.glob("suite.*") if f.suffix in (".yaml", ".yml")]
        if suite_files:
            suite = load_suite(suite_files[0])
            print(f"📋 套件模式: {suite.get('name', '未命名')}")
            print(f"📦 共 {len(suite['resolved_cases'])} 个场景\n")
            return await run_suite(suite, config)

        cases = load_directory(path)
    else:
        cases = [load_case(path)]

    print(f"共加载 {len(cases)} 个用例\n")
    results = []

    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] 执行: {case['name']}")
        r = await run_case(case, config)
        status_icon = "PASS" if r["status"] == "passed" else "FAIL"
        print(f"  结果: {status_icon}\n")
        results.append(r)

    return results
