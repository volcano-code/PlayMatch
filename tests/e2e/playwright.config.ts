import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

export default defineConfig({
  testDir: './specs',
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 40_000,
  expect: { timeout: 10_000 },
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:3100',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    launchOptions: process.env.PLAYMATCH_CHROMIUM_PATH ? {
      executablePath: process.env.PLAYMATCH_CHROMIUM_PATH,
      args: ['--no-sandbox', '--disable-dev-shm-usage', '--no-zygote'],
    } : {},
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: [
    {
      command: 'uv run uvicorn playmatch.main:app --host 127.0.0.1 --port 8100',
      cwd: root,
      url: 'http://127.0.0.1:8100/health',
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      env: {
        DATABASE_URL: 'sqlite:///./.state/e2e.db',
        ALLOWED_ORIGINS: 'http://127.0.0.1:3100',
        COOKIE_SECURE: 'false',
        LLM_API_KEY: '',
        LLM_MODEL: '',
        LLM_BASE_URL: '',
      },
    },
    {
      command: 'npm --prefix apps/web run start -- --hostname 127.0.0.1 --port 3100',
      cwd: root,
      url: 'http://127.0.0.1:3100',
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      env: { API_INTERNAL_URL: 'http://127.0.0.1:8100', NEXT_TELEMETRY_DISABLED: '1' },
    },
  ],
});
