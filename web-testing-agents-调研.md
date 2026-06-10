# GitHub Web 端测试 AI Agent 项目调研

> 调研日期：2026-06-08
> 主题：测试 Web 端的 AI Agent 开源项目

---

## 🥇 通用浏览器自动化 Agent（QA/测试常用）

| 项目 | 说明 | Star 量级 |
|------|------|----------|
| **[browser-use/browser-use](https://github.com/browser-use/browser-use)** | 最火的一个。Python 库，让 LLM（GPT-4o/Claude）像人一样读屏幕、操作网页。常见用法就是测试注册/登录流程。MIT 协议 | ⭐ 极高 |
| **[Skyvern-AI/skyvern](https://github.com/Skyvern-AI/skyvern)** | Playwright 扩展 + Vision LLM，用自然语言驱动。无需预设 XPath，**对 UI 改版有抗性**，适合多步工作流 | ⭐ 高 |
| **[vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser)** | CLI 工具，专为编码助手设计（支持 Claude Code/Cursor/Copilot 等）。**安全特性强**：LLM 看不到密码、有域名白名单 | ⭐ 中 |

---

## 🎯 专门做「Web 测试」的 Agent（最贴合需求）

| 项目 | 说明 |
|------|------|
| **[agentlabs-dev/auto-inspector](https://github.com/agentlabs-dev/auto-inspector)** | 自主测试 Agent —— 你写 user story，它自动测网站并出报告。CLI + Web 双形态，Apache 2.0 |
| **[testdriverai](https://github.com/testdriverai)** | 像「你自己的 QA 员工」。AI 识屏 + 操作鼠标键盘，黑盒测试**无需 test-id/选择器**，YAML 脚本，深度集成 GitHub Actions CI/CD，带视频回放 |
| **[takahirom/arbigent](https://github.com/takahirom/arbigent)** | 同时测 **Android/iOS/Web**，5 分钟上手。亮点是把复杂任务**拆解成小场景**，解决 AI 乱点按钮的问题 |
| **[Top-Q/agent-q](https://github.com/Top-Q/agent-q)** | 基于 SmolAgents + Playwright，动态生成 Python 测试代码并**缓存复用**（成功的代码存下来，下次不再调 LLM） |

---

## 🛠 测试用例 / 脚本生成框架

| 项目 | 说明 |
|------|------|
| **[mindfiredigital/AUTOTEST](https://github.com/mindfiredigital/AUTOTEST)** | GenAI 框架，动态分析网页后生成**测试用例 + Selenium 脚本**，LLM 抽取失败时回退到标准选择器 |

---

## 📚 精选清单 & Benchmark（值得收藏）

- **[tugkanboz/awesome-ai-testing](https://github.com/tugkanboz/awesome-ai-testing)** — AI 测试工具大全（测试生成、自愈、MCP 测试、LLM 评估）
- **[autonomous-testing/testing-autonomy](https://github.com/autonomous-testing/testing-autonomy)** — 自主测试工具清单
- **[Agent-Tools/awesome-autonomous-web](https://github.com/Agent-Tools/awesome-autonomous-web)** — 含 WebArena / VisualWebArena / WebCanvas 等评测基准

---

## 💡 结合本项目（browser-test-agent）的建议

本项目流水线（探索 → 生成用例 → UI 自动化 → 执行报告）与上述项目思路高度一致，最值得参考的是：

1. **auto-inspector / testdriver.ai** —— 思路最接近「user story → 自动测 → 出报告」
2. **arbigent 的「场景拆解」** —— 对应 P3 的「场景化 UI 用例」设计，能解决 AI 乱点的痛点
3. **agent-q 的「代码缓存复用」** —— 可借鉴到 P4 执行器，降低 LLM 调用成本

---

## ⚠️ 共性提醒

这些项目所谓的「开源」大多只开源了**本地 runner/agent**，核心智能仍依赖外部商业 LLM API —— 选型时注意**成本和数据隐私**。
