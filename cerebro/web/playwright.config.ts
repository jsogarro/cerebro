import { defineConfig, devices } from '@playwright/test';
import process from 'node:process';

const host = process.env.PLAYWRIGHT_HOST ?? '127.0.0.1';
const port = process.env.PLAYWRIGHT_PORT ?? '5173';
const baseURL = `http://${host}:${port}`;
const backendURL =
    process.env.PLAYWRIGHT_BACKEND_URL ??
    process.env.VITE_API_URL ??
    'http://127.0.0.1:8000';

export default defineConfig({
    testDir: './tests/e2e',
    fullyParallel: true,
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 2 : 0,
    workers: process.env.CI ? 1 : undefined,
    reporter: 'html',
    use: {
        baseURL,
        reducedMotion: 'reduce',
        trace: 'on-first-retry',
    },
    projects: [
        {
            name: 'chromium',
            use: { ...devices['Desktop Chrome'] },
        },
    ],
    webServer: {
        command: `npm run dev -- --host ${host} --port ${port}`,
        url: baseURL,
        env: {
            VITE_API_URL: backendURL,
        },
        reuseExistingServer: !process.env.CI && !process.env.PLAYWRIGHT_ISOLATED,
    },
});
