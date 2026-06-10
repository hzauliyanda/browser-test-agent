import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type { Page } from 'playwright';
import type { TestCase, Step, Suite, SuiteMeta } from './loader.js';
import { loadConfig } from './config.js';

export interface StepResult {
  stepIndex: number;
  status: 'passed' | 'failed';
  error?: string;
  screenshotBefore?: string;
  screenshotAfter?: string;
}

export interface CaseResult {
  name: string;
  source: string;
  status: 'passed' | 'failed' | 'skipped';
  error: string | null;
  screenshot_dir: string | null;
  timestamp: string;
  tc_ids: string[];
  depends_on: string[];
  suite_meta: SuiteMeta | Record<string, never>;
  steps: StepResult[];
}

interface Agent {
  aiAct(action: string): Promise<void>;
  aiTap(element: string): Promise<void>;
  aiInput(value: string, element: string): Promise<void>;
  aiAssert(assertion: string): Promise<void>;
  aiWaitFor(condition: string, opts?: { timeoutMs?: number }): Promise<void>;
  aiScroll(opts: { scrollType: string }, element?: string): Promise<void>;
  aiQuery<T>(query: string): Promise<T>;
  aiBoolean(question: string): Promise<boolean>;
  destroy(): Promise<void>;
}

const OUTPUT_DIR = path.resolve('output');

// 这些动作执行后「理应」让画面发生变化；若连续同屏则疑似卡屏
const CHANGING_ACTIONS = new Set(['click', 'navigate', 'select', 'input']);

async function waitForManualLogin(page: Page, maxWaitSec: number): Promise<boolean> {
  const checkInterval = 3000;
  const maxChecks = Math.floor((maxWaitSec * 1000) / checkInterval);
  for (let i = 0; i < maxChecks; i++) {
    await page.waitForTimeout(checkInterval);
    const url = page.url();
    // 不再在登录页 → 认为已登录
    if (!url.includes('login') && !url.includes('sso') && !url.includes('passport')) {
      return true;
    }
    // 尝试检测页面是否有登录表单（没有则认为已跳转）
    const hasLoginForm = await page.evaluate(() => {
      const body = document.body?.innerText || '';
      return body.includes('账号') && body.includes('密码') && body.includes('登录');
    });
    if (!hasLoginForm) {
      return true;
    }
  }
  return false;
}

function timestamp(): string {
  return new Date().toISOString().replace(/[-:T]/g, '').slice(0, 15);
}

async function takeScreenshot(page: Page, filePath: string): Promise<void> {
  try {
    await page.screenshot({ path: filePath, fullPage: false });
  } catch { /* ignore */ }
}

/** 实时截一帧并算签名，用于判断画面是否还在变化 */
async function screenSignature(page: Page): Promise<string> {
  try {
    const buf = await page.screenshot({ fullPage: false });
    return crypto.createHash('md5').update(buf).digest('hex');
  } catch {
    return '';
  }
}

/** 对已落盘的截图文件算签名，复用 before/after 截图做卡屏判定，避免额外截图开销 */
function fileSignature(filePath: string): string {
  try {
    return crypto.createHash('md5').update(fs.readFileSync(filePath)).digest('hex');
  } catch {
    return '';
  }
}

/**
 * 等页面渲染稳定：轮询截图签名，连续两次相同即认为已稳定并提前返回，
 * 最多等 maxPolls*interval 毫秒。替代 navigate/wait 后的固定 sleep ——
 * 快页面早走、慢页面多等，既不死等也不空等（卡屏检测的基础）。
 */
async function waitForStable(page: Page, maxPolls = 12, interval = 500): Promise<void> {
  let prev = '';
  for (let i = 0; i < maxPolls; i++) {
    await page.waitForTimeout(interval);
    const sig = await screenSignature(page);
    if (sig && sig === prev) return;
    prev = sig;
  }
}

/**
 * 健壮的滚动：
 * 1) 选「可滚动空间最大」的容器（而非深度优先第一个），避免滚错侧边栏/小面板
 * 2) 不用 smooth（异步会让后续判断与定位失效）
 * 3) 再用 鼠标移到内容区中央 + 滚轮 兜底，解决 overflow 被内层吃掉的情况
 * 返回 DOM 层面是否真的发生了滚动
 */
async function robustScroll(page: Page, direction: 'down' | 'up'): Promise<boolean> {
  const moved = await page.evaluate((dir: 'down' | 'up') => {
    const doc = (document.scrollingElement || document.documentElement) as HTMLElement;
    let best: HTMLElement = doc;
    let bestGap = doc.scrollHeight - doc.clientHeight;
    document.querySelectorAll<HTMLElement>('*').forEach((el) => {
      const s = getComputedStyle(el);
      if (s.overflowY === 'auto' || s.overflowY === 'scroll') {
        const gap = el.scrollHeight - el.clientHeight;
        if (gap > bestGap + 10) { bestGap = gap; best = el; }
      }
    });
    const before = best.scrollTop;
    const delta = (best.clientHeight || 600) * 0.8 * (dir === 'down' ? 1 : -1);
    best.scrollBy({ top: delta }); // 不用 smooth
    return best.scrollTop !== before;
  }, direction);

  const vp = page.viewportSize();
  if (vp) await page.mouse.move(vp.width / 2, vp.height / 2);
  await page.mouse.wheel(0, direction === 'down' ? 900 : -900);
  await page.waitForTimeout(400);
  return moved;
}

/** 从自然语言步骤描述里抠出元素关键词，仅用于 Playwright 兜底定位 */
function extractKeyword(desc: string): string {
  return desc
    .replace(/^(请|点击|单击|选择|勾选|输入|填写|在|打开|进入)+/g, '')
    .replace(/(按钮|链接|输入框|文本框|下拉框|下拉菜单|选项|图标|标签|页签|tab)+$/gi, '')
    .replace(/[，。、：:'"'']/g, '')
    .trim();
}

/**
 * 智能点击：先 Midscene 视觉点击（可读自然语言），失败则向下滚动重试，
 * 最后一次用 Playwright 语义定位（容器感知，自动 scrollIntoView，专治屏外按钮）。
 * retries 来自 config.yaml 的 retry.max_attempts（总尝试次数 = retries + 1，至少 2 次以保留滚动+兜底）。
 */
async function smartClick(page: Page, agent: Agent, desc: string, retries: number): Promise<void> {
  const total = Math.max(retries + 1, 2);
  for (let attempt = 0; attempt < total; attempt++) {
    try {
      await agent.aiTap(desc);
      return;
    } catch (e) {
      if (attempt < total - 1) {
        await robustScroll(page, 'down');
      } else {
        // 最后一次：Playwright 语义兜底，按关键词找 button/link/text，自动滚到视口再点
        const kw = extractKeyword(desc) || desc;
        const candidates = [
          page.getByRole('button', { name: kw }),
          page.getByRole('link', { name: kw }),
          page.getByText(kw, { exact: false }),
        ];
        for (const loc of candidates) {
          if ((await loc.count()) > 0) {
            const target = loc.first();
            await target.scrollIntoViewIfNeeded();
            await target.click();
            return;
          }
        }
        throw e; // 都没找到，抛出原始 Midscene 错误
      }
    }
  }
}

/**
 * 智能输入：aiInput 失败先向下滚动再重试（输入框常在折叠线以下），耗尽 retries 才抛错。
 */
async function smartInput(page: Page, agent: Agent, value: string, desc: string, retries: number): Promise<void> {
  const total = Math.max(retries + 1, 2);
  for (let attempt = 0; attempt < total; attempt++) {
    try {
      await agent.aiInput(value, desc);
      return;
    } catch (e) {
      if (attempt < total - 1) await robustScroll(page, 'down');
      else throw e;
    }
  }
}

/**
 * 智能选择：select 走 aiAct 自然语言，失败重试（下拉项可能异步渲染）。
 * 最后一次重试仍失败则用 Playwright 兜底：先点击展开下拉框（兼容自定义组件），再选选项。
 */
async function smartSelect(page: Page, agent: Agent, desc: string, value: string | undefined, retries: number): Promise<void> {
  const action = value ? `在「${desc}」中选择「${value}」` : desc;
  const total = Math.max(retries + 1, 1);
  let lastErr: unknown;
  for (let attempt = 0; attempt < total; attempt++) {
    try {
      await agent.aiAct(action);
      return;
    } catch (e) {
      lastErr = e;
    }
  }

  // Playwright 兜底：先点开下拉框，再选选项（兼容自定义组件）
  if (value) {
    console.log(`  🔄 aiAct 失败，尝试 Playwright 兜底：desc="${desc}" value="${value}"`);

    // 先找下拉框的触发元素（可能是 label、文字、图标等）
    const dropTriggerCandidates = [
      page.getByLabel(desc),                              // label 关联
      page.getByText(desc).first(),                        // 文本匹配
      page.getByRole('combobox', { name: desc }),         // combobox 角色
      page.getByRole('button', { name: desc }),           // 按钮样式下拉
      page.locator(`select`).filter({ hasText: desc }),  // 标准 select
      // 去掉「选择」「请」等前缀再试
      page.getByLabel(desc.replace(/^(选择|请|在)/, '')),
      page.getByText(desc.replace(/^(选择|请|在)/, '')).first(),
    ];

    let triggerFound = false;
    for (const loc of dropTriggerCandidates) {
      try {
        const count = await loc.count();
        if (count > 0) {
          const trigger = loc.first();
          // 先点开下拉框
          await trigger.click();
          await page.waitForTimeout(500); // 等选项渲染
          triggerFound = true;
          console.log(`    ✅ 下拉框已展开`);
          break;
        }
      } catch {
        /* 继续试下一个候选 */
      }
    }

    if (!triggerFound) {
      console.log(`    ❌ 找不到下拉框触发元素，尝试滚动后再找`);
      await robustScroll(page, 'down');
      // 滚动后再找一次
      for (const loc of [page.getByLabel(desc), page.getByText(desc).first()]) {
        const count = await loc.count();
        if (count > 0) {
          await loc.first().click();
          await page.waitForTimeout(500);
          triggerFound = true;
          console.log(`    ✅ 滚动后找到并展开`);
          break;
        }
      }
    }

    if (triggerFound) {
      // 下拉框展开后，尝试点选选项（多种定位策略 + 更长等待）
      await page.waitForTimeout(800); // 等选项充分渲染

      // 调试：打印下拉框附近 DOM 片段，看选项实际结构
      try {
        const bodyText = await page.evaluate(() => document.body.innerText);
        const relevant = bodyText.split('\n').filter((l: string) =>
          l.includes('立即') || l.includes('执行') || l.includes('类型')
        ).slice(0, 20);
        console.log(`    📋 下拉框附近文本片段: ${JSON.stringify(relevant)}`);
      } catch {
        /* 忽略调试失败 */
      }

      const optionCandidates = [
        page.getByRole('option', { name: value }),
        page.getByText(value, { exact: true }).first(),
        page.getByText(value, { exact: false }).first(), // 模糊匹配
        page.locator(`li`).filter({ hasText: value }).first(),
        page.locator(`div[role="option"]`).filter({ hasText: value }).first(),
        page.locator(`div`).filter({ hasText: value }).first(), // 自定义组件常用 div
        page.locator(`*`).filter({ hasText: value }).first(), // 兜底：任意包含该文本的元素
      ];
      for (const opt of optionCandidates) {
        try {
          const count = await opt.count();
          console.log(`    选项候选 count: ${count}`);
          if (count > 0) {
            await opt.first().click();
            console.log(`    ✅ 已选择选项 "${value}"`);
            return;
          }
        } catch {
          /* 继续试下一个 */
        }
      }
      console.log(`    ❌ 找不到选项 "${value}"，所有候选定位器都失败`);
    } else {
      console.log(`    ❌ 找不到下拉框触发元素`);
    }
  }

  throw lastErr;
}

/**
 * 智能断言：assert 失败不再无脑判失败。
 * 先用 aiWaitFor 等断言条件出现（取证），再重试 aiAssert——
 * 这样能区分「时序未就绪」（等一下就过）与「真失败」（等到超时仍不满足）。
 * 仅当 retries 全部耗尽仍不通过，才抛错让上层 break。
 */
async function smartAssert(page: Page, agent: Agent, desc: string, retries: number): Promise<void> {
  const total = Math.max(retries + 1, 1);
  let lastErr: unknown;
  for (let attempt = 0; attempt < total; attempt++) {
    try {
      await agent.aiAssert(desc);
      return;
    } catch (e) {
      lastErr = e;
      if (attempt < total - 1) {
        // 取证：给页面时间渲染，等断言条件出现再重试；等不到也继续重试（不吞错）
        try {
          await agent.aiWaitFor(desc, { timeoutMs: 8000 });
        } catch {
          await page.waitForTimeout(800);
        }
      }
    }
  }
  throw lastErr;
}

async function executeStep(
  page: Page,
  agent: Agent,
  step: Step,
  baseUrl?: string,
  retries = 0,
): Promise<void> {
  const desc = step.description || step.target || '';

  switch (step.action) {
    case 'navigate': {
      let url = step.url || step.target || '';
      if (!url) throw new Error('navigate 缺少 url');
      if (baseUrl && !url.startsWith('http')) {
        url = baseUrl.replace(/\/+$/, '') + '/' + url.replace(/^\/+/, '');
      }
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await waitForStable(page);
      break;
    }
    case 'click': {
      await smartClick(page, agent, desc, retries);
      break;
    }
    case 'input': {
      const value = step.value || '';
      await smartInput(page, agent, value, desc, retries);
      break;
    }
    case 'assert': {
      await smartAssert(page, agent, desc, retries);
      break;
    }
    case 'wait': {
      if (step.seconds) {
        await page.waitForTimeout(step.seconds * 1000);
      } else if (desc) {
        await agent.aiWaitFor(desc, { timeoutMs: 10000 });
      } else {
        await waitForStable(page);
      }
      break;
    }
    case 'scroll': {
      const direction = (step.direction === 'up' ? 'up' : 'down') as 'down' | 'up';
      // 如果给了目标文字，直接把目标元素滚进视口（容器感知，最可靠）
      const targetText = step.value || (step.target && step.target !== desc ? step.target : '');
      if (targetText) {
        const loc = page.getByText(targetText, { exact: false }).first();
        if ((await loc.count()) > 0) {
          await loc.scrollIntoViewIfNeeded();
          await page.waitForTimeout(400);
          break;
        }
      }
      const times = step.times || 3;
      for (let i = 0; i < times; i++) {
        await robustScroll(page, direction);
      }
      break;
    }
    case 'select': {
      await smartSelect(page, agent, desc, step.value, retries);
      break;
    }
    default:
      throw new Error(`未知的 action: ${step.action}`);
  }
}

export async function executeCase(
  page: Page,
  agent: Agent,
  testCase: TestCase,
  screenshotDir: string,
): Promise<CaseResult> {
  const ts = timestamp();
  const caseName = testCase.name.replace(/\s+/g, '_');
  const caseScreenshotDir = path.join(screenshotDir, `${caseName}_${ts}`);
  fs.mkdirSync(caseScreenshotDir, { recursive: true });

  const suiteMeta = testCase._suite_meta || {};
  const tcIds = testCase.tc_ids || suiteMeta.tc_ids || [];
  const baseUrl = testCase.base_url;
  const retries = loadConfig().retry.max_attempts;

  const result: CaseResult = {
    name: testCase.name,
    source: testCase._source || '',
    status: 'pending',
    error: null,
    screenshot_dir: caseScreenshotDir,
    timestamp: ts,
    tc_ids: tcIds,
    depends_on: suiteMeta.depends_on || [],
    suite_meta: suiteMeta,
    steps: [],
  };

  let lastSig = ''; // 上一步执行后的画面签名，用于跨步骤卡屏检测

  for (let i = 0; i < testCase.steps.length; i++) {
    const step = testCase.steps[i];
    const stepResult: StepResult = { stepIndex: i, status: 'passed' };

    const beforePath = path.join(caseScreenshotDir, `step_${String(i + 1).padStart(3, '0')}_before.png`);
    await takeScreenshot(page, beforePath);
    stepResult.screenshotBefore = beforePath;

    try {
      await executeStep(page, agent, step, baseUrl, retries);
      result.status = 'passed';
    } catch (e: any) {
      stepResult.status = 'failed';
      stepResult.error = e.message || String(e);
      result.status = 'failed';
      result.error = `步骤 ${i + 1} (${step.action}) 失败: ${stepResult.error}`;

      const failPath = path.join(caseScreenshotDir, `step_${String(i + 1).padStart(3, '0')}_fail.png`);
      await takeScreenshot(page, failPath);
      stepResult.screenshotAfter = failPath;

      result.steps.push(stepResult);
      break;
    }

    const afterPath = path.join(caseScreenshotDir, `step_${String(i + 1).padStart(3, '0')}_after.png`);
    await takeScreenshot(page, afterPath);
    stepResult.screenshotAfter = afterPath;

    // 卡屏检测：复用 before/after 截图算签名。
    // 当一个「理应改变画面」的动作做完后，画面相对动作前没变（before==after），
    // 且与上一步结束时也完全相同（连续同屏）→ 疑似卡屏，主动等页面稳定，替代盲目继续。
    const beforeSig = fileSignature(beforePath);
    const afterSig = fileSignature(afterPath);
    if (
      CHANGING_ACTIONS.has(step.action) &&
      afterSig &&
      afterSig === beforeSig &&
      afterSig === lastSig
    ) {
      console.log(`  🧊 疑似卡屏：步骤 ${i + 1} (${step.action}) 后画面连续无变化，主动等待页面稳定...`);
      await waitForStable(page);
    }
    lastSig = afterSig || lastSig;

    result.steps.push(stepResult);
  }

  return result;
}

export async function executeLogin(
  page: Page,
  agent: Agent,
  loginSteps: Step[],
  loginUrl?: string,
): Promise<void> {
  if (loginUrl) {
    await page.goto(loginUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(3000);
  }

  for (const step of loginSteps) {
    await executeStep(page, agent, step);
  }

  await page.waitForTimeout(2000);
}

function extractScenarioId(name: string): string | null {
  const m = name.match(/^(S\d+)/i);
  return m ? m[1].toUpperCase() : null;
}

export async function executeSuite(
  page: Page,
  agent: Agent,
  suite: Suite,
): Promise<CaseResult[]> {
  const results: CaseResult[] = [];
  const screenshotDir = path.join(OUTPUT_DIR, 'screenshots');
  const failedScenarios = new Set<string>();

  if (suite.login.enabled) {
    console.log('🔐 执行登录...');
    try {
      await executeLogin(page, agent, suite.login.steps, suite.login.url);
      console.log('  ✅ 登录成功\n');
    } catch (e: any) {
      console.log(`  ⚠️  自动登录失败: ${e.message}`);
      console.log('  👤 请在弹出的浏览器窗口中手动完成登录...');
      // 等待用户手动登录，最多等 120 秒
      const loggedIn = await waitForManualLogin(page, 120);
      if (loggedIn) {
        console.log('  ✅ 手动登录成功\n');
      } else {
        console.log(`  ❌ 登录超时\n`);
        for (const testCase of suite.resolved_cases) {
          const meta = testCase._suite_meta || ({} as SuiteMeta);
          results.push({
            name: testCase.name,
            source: testCase._source || '',
            status: 'skipped',
            error: `登录失败: 手动登录超时`,
            screenshot_dir: null,
            timestamp: timestamp(),
            tc_ids: testCase.tc_ids || meta.tc_ids || [],
            depends_on: meta.depends_on || [],
            suite_meta: meta,
            steps: [],
          });
        }
        return results;
      }
    }
  }

  const cases = suite.resolved_cases;
  for (let i = 0; i < cases.length; i++) {
    const testCase = cases[i];
    const meta = testCase._suite_meta || ({} as SuiteMeta);
    const tcIds = testCase.tc_ids || meta.tc_ids || [];
    const dependsOn = meta.depends_on || [];
    const scenarioId = extractScenarioId(testCase.name);

    const skippedDeps = dependsOn.filter(d => failedScenarios.has(d));
    if (skippedDeps.length > 0) {
      console.log(`[${i + 1}/${cases.length}] ⏭️  跳过: ${testCase.name}（依赖 ${skippedDeps.join(', ')} 失败）`);
      results.push({
        name: testCase.name,
        source: testCase._source || '',
        status: 'skipped',
        error: `依赖场景失败: ${skippedDeps.join(', ')}`,
        screenshot_dir: null,
        timestamp: timestamp(),
        tc_ids: tcIds,
        depends_on: dependsOn,
        suite_meta: meta,
        steps: [],
      });
      if (scenarioId) failedScenarios.add(scenarioId);
      continue;
    }

    console.log(`[${i + 1}/${cases.length}] 执行: ${testCase.name}`);
    const r = await executeCase(page, agent, testCase, screenshotDir);
    const icon = r.status === 'passed' ? 'PASS' : 'FAIL';
    console.log(`  结果: ${icon}\n`);
    results.push(r);

    if (r.status === 'failed' && scenarioId) {
      failedScenarios.add(scenarioId);
    }
  }

  return results;
}
