# browser-test-agent

AI 驱动的浏览器自动化测试 Agent。用 **YAML / JSON 描述测试用例**（自然语言描述元素，无需写选择器），由大模型理解页面并执行点击、输入、断言等操作。

提供两套执行引擎，共用同一份用例格式：

| 引擎 | 技术栈 | 入口 |
|------|--------|------|
| **TypeScript runner** | [Midscene.js](https://midscenejs.com/) + Playwright + VLM | `runner/index.ts` |
| **Python agent** | [browser-use](https://github.com/browser-use/browser-use) + LiteLLM | `src/main.py` |

## 特性

- **自然语言定位元素**：用例里写「登录按钮」「密码输入框」，由模型在页面上找到对应元素，不依赖脆弱的 CSS/XPath 选择器。
- **测试套件 + 依赖管理**：`suite.yaml` 统一登录一次后批量执行多个用例，支持 `depends_on`（前置场景失败则跳过后续）。
- **Locator 缓存**：命中缓存的步骤复用上次的定位结果，回归测试提速、省 token（`config.yaml` 的 `cache`）。
- **自愈学习**：步骤失败时自动重试兜底，可选地把成功的定位回写到用例（`config.yaml` 的 `retry` / `learning`）。
- **环境变量注入**：用例中的 `${VAR}` 在加载时替换为环境变量，凭证等敏感信息放 `.env`，不进仓库。
- **报告与录屏**：执行后生成测试报告，可选录制视频（`config.yaml` 的 `browser.record_video`）。

## 快速开始

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入模型 API Key 与测试账号 TEST_USERNAME / TEST_PASSWORD
```

### 2A. TypeScript runner（Midscene + Playwright）

```bash
npm install
npx playwright install chromium

# 运行单个用例 / 目录 / 套件
npm run run -- testcases/example.yaml
npm run run -- testcases/suite.yaml
npm run run -- testcases/suite.yaml -- --headless
```

### 2B. Python agent（browser-use）

```bash
uv sync           # 或 pip install -e .
python run.py testcases/example.yaml
python run.py testcases/ --headless --llm openai-compatible --model openai/gpt-5.5
```

## 用例格式

### 标准用例（YAML）

```yaml
name: "百度搜索测试"
base_url: "https://www.baidu.com"
steps:
  - action: navigate
    url: "/"
  - action: input
    description: "搜索输入框"      # 自然语言描述目标元素
    value: "${SEARCH_KEYWORD}"    # ${VAR} 会被替换为环境变量
  - action: click
    description: "百度一下按钮"
  - action: assert
    description: "搜索结果页面应显示结果列表"
```

支持的 `action`：`navigate` · `input` · `click` · `assert` · `wait` · `scroll` · `select`

### 测试套件（suite.yaml）

```yaml
name: "处罚任务管理 UI 测试套件"
base_url: "https://example.com/app"
login:                 # 执行所有用例前先统一登录一次
  enabled: true
  url: "https://example.com/app/login"
  steps:
    - action: input
      description: "用户名输入框"
      value: "${TEST_USERNAME}"   # 凭证来自 .env，切勿硬编码
    - action: input
      description: "密码输入框"
      value: "${TEST_PASSWORD}"
    - action: click
      description: "登录按钮"
cases:
  - file: s1-create-task.yaml
    name: "S1: 任务创建全流程"
    depends_on: []
  - file: s2-task-list.yaml
    name: "S2: 任务列表查询"
    depends_on: ["S1"]   # S1 失败则跳过 S2
```

> 也支持外部录制工具导出的 `[{step, target, type, input}]` 数组格式（见 `testcases/risk-test.json`），加载时会自动归一化。

## 配置（config.yaml）

```yaml
llm:
  provider: "openai-compatible"
  model: "openai/gpt-5.5"
  api_base: "https://api.example.dev/v1"
browser:
  headless: false
  viewport: { width: 1440, height: 900 }
  record_video: true
retry:
  max_attempts: 2          # 步骤失败重试次数
cache:
  enabled: true            # Locator 缓存，回归提速
learning:
  enabled: true            # 自愈学习
  auto_update: true        # 自动回写成功定位到用例（谨慎开启）
```

## 项目结构

```
runner/        TypeScript 执行引擎（Midscene + Playwright）
src/           Python 执行引擎（browser-use）
testcases/     YAML / JSON 测试用例与套件
config.yaml    运行配置
.env.example   环境变量模板（复制为 .env 填值）
```

## 安全约定

- **不要把账号、密码、API Key 硬编码进用例或提交到仓库。** 一律使用 `${VAR}` 占位 + `.env`。
- `.env`、`node_modules/`、`.venv/`、`output/`、`midscene_run/`、`.browser-data/` 等已在 `.gitignore` 中忽略。
