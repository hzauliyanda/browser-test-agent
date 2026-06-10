import 'dotenv/config';
import path from 'path';
import { chromium, BrowserContext } from 'playwright';
import { PlaywrightAgent } from '@midscene/web/playwright';
import { loadCase, loadSuite, isSuiteFile } from './loader.js';
import { executeCase, executeSuite } from './executor.js';
import { generateReport } from './reporter.js';

const PERSISTENT_DIR = path.resolve(process.cwd(), '.browser-data');

function parseArgs(): { target: string; headless: boolean } {
  const args = process.argv.slice(2);
  let target = '';
  let headless = false;

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--headless') {
      headless = true;
    } else if (!args[i].startsWith('-')) {
      target = args[i];
    }
  }

  if (!target) {
    console.error('用法: npx tsx runner/index.ts <yaml路径或目录> [--headless]');
    process.exit(1);
  }

  return { target, headless };
}

async function main() {
  const { target, headless } = parseArgs();

  console.log(`🎯 目标: ${target}\n`);

  const context = await chromium.launchPersistentContext(PERSISTENT_DIR, {
    headless,
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  const page = context.pages()[0] || await context.newPage();

  // 按用例/套件文件名派生 cacheId，开启 Midscene 内置缓存：
  // 命中缓存的步骤直接复用上次的定位/规划，不再调 LLM —— 回归提速、省 token。
  const cacheId = path.basename(target)
    .replace(/\.(ya?ml|json)$/i, '')
    .replace(/[^\w一-龥-]/g, '_') || 'default';

  const agent = new PlaywrightAgent(page, {
    waitForNavigationTimeout: 10000,
    waitForNetworkIdleTimeout: 5000,
    cache: { id: cacheId, strategy: 'read-write' },
  });

  try {
    let results;

    if (isSuiteFile(target)) {
      const suite = loadSuite(target);
      console.log(`📋 套件模式: ${suite.name}`);
      if (suite.login.enabled) {
        console.log(`🔐 将在执行前登录`);
      }
      console.log(`📦 共 ${suite.resolved_cases.length} 个场景\n`);
      results = await executeSuite(page, agent as any, suite);
    } else {
      const testCase = loadCase(target);
      console.log(`共加载 1 个用例\n`);
      console.log(`[1/1] 执行: ${testCase.name}`);
      const screenshotDir = 'output/screenshots';
      results = [await executeCase(page, agent as any, testCase, screenshotDir)];
      const icon = results[0].status === 'passed' ? 'PASS' : 'FAIL';
      console.log(`  结果: ${icon}\n`);
    }

    const reportPath = generateReport(results);
    console.log(`\n报告已保存: ${reportPath}`);

    const failed = results.filter(r => r.status === 'failed').length;
    if (failed > 0) {
      process.exit(1);
    }
  } finally {
    await (agent as any).destroy?.();
    await context.close();
  }
}

main().catch(e => {
  console.error('执行出错:', e);
  process.exit(1);
});
