import fs from 'node:fs';
import path from 'node:path';
import yaml from 'js-yaml';

export interface RunnerConfig {
  retry: { max_attempts: number };
  browser: { headless: boolean; record_video: boolean };
}

const DEFAULTS: RunnerConfig = {
  retry: { max_attempts: 2 },
  browser: { headless: false, record_video: false },
};

let cached: RunnerConfig | null = null;

/**
 * 读取项目根目录的 config.yaml；缺字段/读取失败一律回退默认值。
 * 之前 config.yaml 从未被代码消费，retry 配置形同虚设——这里把它接通。
 */
export function loadConfig(): RunnerConfig {
  if (cached) return cached;
  const p = path.resolve(process.cwd(), 'config.yaml');
  let raw: any = {};
  try {
    if (fs.existsSync(p)) raw = yaml.load(fs.readFileSync(p, 'utf-8')) || {};
  } catch {
    /* 配置坏了不致命，用默认值 */
  }
  cached = {
    retry: {
      max_attempts: Number.isFinite(raw?.retry?.max_attempts)
        ? raw.retry.max_attempts
        : DEFAULTS.retry.max_attempts,
    },
    browser: {
      headless: raw?.browser?.headless ?? DEFAULTS.browser.headless,
      record_video: raw?.browser?.record_video ?? DEFAULTS.browser.record_video,
    },
  };
  return cached;
}
