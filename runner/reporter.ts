import fs from 'node:fs';
import path from 'node:path';
import type { CaseResult } from './executor.js';

const OUTPUT_DIR = path.resolve('output');

export function generateReport(results: CaseResult[]): string {
  const ts = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 15);
  const reportDir = path.join(OUTPUT_DIR, 'reports');
  fs.mkdirSync(reportDir, { recursive: true });
  const reportPath = path.join(reportDir, `report_${ts}.txt`);

  const total = results.length;
  const passed = results.filter(r => r.status === 'passed').length;
  const failed = results.filter(r => r.status === 'failed').length;
  const skipped = results.filter(r => r.status === 'skipped').length;

  const lines: string[] = [
    '='.repeat(60),
    `  测试报告  ${ts}`,
    '='.repeat(60),
    `  总计: ${total}  通过: ${passed}  失败: ${failed}  跳过: ${skipped}`,
    '-'.repeat(60),
  ];

  if (hasSuiteMeta(results)) {
    lines.push(...generateSuiteDetail(results));
  } else {
    lines.push(...generateSimpleDetail(results));
  }

  const tcSummary = generateTcSummary(results);
  if (tcSummary.length > 0) {
    lines.push('='.repeat(60));
    lines.push('  功能用例 (TC-ID) 测试结果汇总');
    lines.push('-'.repeat(60));
    lines.push(...tcSummary);
  }

  lines.push('='.repeat(60));

  const text = lines.join('\n');
  fs.writeFileSync(reportPath, text, 'utf-8');
  console.log(text);
  return reportPath;
}

function hasSuiteMeta(results: CaseResult[]): boolean {
  return results.some(r => Object.keys(r.suite_meta || {}).length > 0 || r.tc_ids.length > 0);
}

const STATUS_ICON: Record<string, string> = {
  passed: 'PASS',
  failed: 'FAIL',
  skipped: 'SKIP',
};

function generateSimpleDetail(results: CaseResult[]): string[] {
  const lines: string[] = [];
  for (const r of results) {
    const icon = STATUS_ICON[r.status] || '????';
    lines.push(`  [${icon}] ${r.name}`);
    if (r.error) lines.push(`        错误: ${r.error}`);
    if (r.screenshot_dir) lines.push(`        截图: ${r.screenshot_dir}`);
    lines.push('');
  }
  return lines;
}

function generateSuiteDetail(results: CaseResult[]): string[] {
  const lines: string[] = [];
  for (const r of results) {
    const icon = STATUS_ICON[r.status] || '????';
    let detailLine = `  [${icon}] ${r.name}`;
    if (r.depends_on.length > 0) {
      detailLine += `  (依赖: ${r.depends_on.join(', ')})`;
    }
    lines.push(detailLine);

    if (r.tc_ids.length > 0) {
      const tcStatus = r.status === 'passed' ? '✅' : r.status === 'failed' ? '❌' : '⏭️';
      lines.push(`        功能用例: ${r.tc_ids.join(', ')}  ${tcStatus}`);
    }

    if (r.error) lines.push(`        错误: ${r.error}`);
    if (r.screenshot_dir) lines.push(`        截图: ${r.screenshot_dir}`);
    lines.push('');
  }
  return lines;
}

function generateTcSummary(results: CaseResult[]): string[] {
  const statusOrder: Record<string, number> = { failed: 0, skipped: 1, passed: 2 };
  const statusLabel: Record<string, string> = { passed: 'PASS', failed: 'FAIL', skipped: 'SKIP' };

  const tcResults: Record<string, { status: string; scenarios: string[] }> = {};

  for (const r of results) {
    for (const tcId of r.tc_ids) {
      if (!tcResults[tcId]) {
        tcResults[tcId] = { status: r.status, scenarios: [] };
      }
      if ((statusOrder[r.status] ?? 3) < (statusOrder[tcResults[tcId].status] ?? 3)) {
        tcResults[tcId].status = r.status;
      }
      tcResults[tcId].scenarios.push(r.name);
    }
  }

  if (Object.keys(tcResults).length === 0) return [];

  const lines: string[] = [];
  const sorted = Object.entries(tcResults).sort(
    (a, b) => (statusOrder[a[1].status] ?? 3) - (statusOrder[b[1].status] ?? 3)
  );

  for (const [tcId, info] of sorted) {
    const icon = statusLabel[info.status] || '????';
    const scenarios = [...new Set(info.scenarios)].join(', ');
    lines.push(`  [${icon}] ${tcId}  <- ${scenarios}`);
  }

  const tcTotal = Object.keys(tcResults).length;
  const tcPassed = Object.values(tcResults).filter(i => i.status === 'passed').length;
  const tcFailed = Object.values(tcResults).filter(i => i.status === 'failed').length;
  const tcSkipped = Object.values(tcResults).filter(i => i.status === 'skipped').length;
  lines.push('');
  lines.push(`  功能用例总计: ${tcTotal}  通过: ${tcPassed}  失败: ${tcFailed}  跳过: ${tcSkipped}`);

  return lines;
}
