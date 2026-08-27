import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/ui',
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  timeout: 45_000,
  expect: {
    timeout: 5_000,
    toHaveScreenshot: {
      animations: 'disabled',
      caret: 'hide',
      scale: 'css',
    },
  },
  outputDir: 'test-results/playwright',
  preserveOutput: 'failures-only',
  reporter: process.env.CI ? 'line' : 'list',
  snapshotPathTemplate:
    '{testDir}/snapshots/{platform}/{testFilePath}/{arg}{ext}',
})
