import { test as base } from '@playwright/test';
import { PlaywrightAiFixture } from '@midscene/web/playwright';
import type { PlayWrightAiFixtureType } from '@midscene/web/playwright';

export const test = base.extend<PlayWrightAiFixtureType>(PlaywrightAiFixture({
  waitForNavigationTimeout: 10000,
  waitForNetworkIdleTimeout: 10000,
}));
