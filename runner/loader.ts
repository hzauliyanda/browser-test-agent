import fs from 'node:fs';
import path from 'node:path';
import yaml from 'js-yaml';

export interface TestCase {
  name: string;
  base_url?: string;
  tc_ids?: string[];
  steps: Step[];
  _source: string;
  _suite_meta?: SuiteMeta;
}

export interface Step {
  action: string;
  description?: string;
  target?: string;
  value?: string;
  url?: string;
  seconds?: number;
  direction?: string;
  times?: number;
}

export interface SuiteMeta {
  suite_name: string;
  depends_on: string[];
  tc_ids: string[];
  case_ref_name: string;
}

export interface Suite {
  name: string;
  base_url?: string;
  login: LoginConfig;
  cases: CaseRef[];
  resolved_cases: TestCase[];
  _suite_dir: string;
}

export interface LoginConfig {
  enabled: boolean;
  url?: string;
  steps: Step[];
}

export interface CaseRef {
  file: string;
  name?: string;
  depends_on?: string[];
  tc_ids?: string[];
}

const VALID_ACTIONS = new Set(['navigate', 'input', 'click', 'assert', 'wait', 'scroll', 'select']);

// 将文本中的 ${VAR} 替换为环境变量值，避免把凭证等敏感信息硬编码进用例。
function expandEnv(text: string): string {
  return text.replace(/\$\{([A-Z0-9_]+)\}/g, (_, name: string) => {
    const val = process.env[name];
    if (val === undefined) {
      throw new Error(`用例引用了未设置的环境变量: ${name}（请在 .env 中配置）`);
    }
    return val;
  });
}

export function loadCase(filepath: string): TestCase {
  const absPath = path.resolve(filepath);
  if (!fs.existsSync(absPath)) {
    throw new Error(`用例文件不存在: ${absPath}`);
  }
  const text = expandEnv(fs.readFileSync(absPath, 'utf-8'));
  const raw = yaml.load(text) as any;
  const testCase = normalize(raw, absPath);
  validate(testCase);
  testCase._source = absPath;
  return testCase;
}

export function loadSuite(filepath: string): Suite {
  const absPath = path.resolve(filepath);
  if (!fs.existsSync(absPath)) {
    throw new Error(`套件文件不存在: ${absPath}`);
  }
  const text = expandEnv(fs.readFileSync(absPath, 'utf-8'));
  const suite = yaml.load(text) as any;

  if (!suite || typeof suite !== 'object') {
    throw new Error('suite.yaml 格式错误：顶层必须是 dict');
  }
  if (!suite.cases) {
    throw new Error('suite.yaml 缺少 cases 字段');
  }

  const suiteDir = path.dirname(absPath);
  const resolved: TestCase[] = [];

  for (const caseRef of suite.cases) {
    const casePath = path.join(suiteDir, caseRef.file);
    const testCase = loadCase(casePath);

    // 从 suite 继承 base_url
    if (!testCase.base_url && suite.base_url) {
      testCase.base_url = suite.base_url;
    }

    // 注入 suite 元数据
    testCase._suite_meta = {
      suite_name: suite.name || '',
      depends_on: caseRef.depends_on || [],
      tc_ids: caseRef.tc_ids || [],
      case_ref_name: caseRef.name || testCase.name,
    };

    if (caseRef.tc_ids && !testCase.tc_ids) {
      testCase.tc_ids = caseRef.tc_ids;
    }

    resolved.push(testCase);
  }

  return {
    name: suite.name || '',
    base_url: suite.base_url,
    login: suite.login || { enabled: false, steps: [] },
    cases: suite.cases,
    resolved_cases: resolved,
    _suite_dir: suiteDir,
  };
}

export function isSuiteFile(filepath: string): boolean {
  const absPath = path.resolve(filepath);
  if (!fs.existsSync(absPath)) return false;
  if (!absPath.endsWith('.yaml') && !absPath.endsWith('.yml')) return false;
  if (path.basename(absPath).toLowerCase().includes('suite')) return true;
  try {
    const text = fs.readFileSync(absPath, 'utf-8');
    const data = yaml.load(text) as any;
    return data && typeof data === 'object' && 'cases' in data;
  } catch {
    return false;
  }
}

function normalize(raw: any, filepath: string): TestCase {
  // 标准 dict 格式
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    return raw as TestCase;
  }
  throw new Error(`用例格式错误 (${filepath})：顶层必须是 dict`);
}

function validate(testCase: TestCase): void {
  if (!testCase.name) throw new Error('用例缺少 name 字段');
  if (!Array.isArray(testCase.steps) || testCase.steps.length === 0) {
    throw new Error('steps 必须是非空列表');
  }
  for (let i = 0; i < testCase.steps.length; i++) {
    const step = testCase.steps[i];
    if (!step.action || !VALID_ACTIONS.has(step.action)) {
      throw new Error(`step ${i} 的 action '${step.action}' 无效，可选: ${[...VALID_ACTIONS].join(', ')}`);
    }
  }
}
