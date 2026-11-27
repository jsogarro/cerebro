import AxeBuilder from '@axe-core/playwright';
import { test, expect } from '@playwright/test';
import { mockApi } from './helpers/mock-api';

const SIGNED_OUT = { cookies: [], origins: [] };
const TOKEN = 'header.payload.sig';

test.describe('sign-in and route guard', () => {
    test.use({ storageState: SIGNED_OUT });

    test('redirects an unauthenticated visitor to the login page', async ({ page }) => {
        await mockApi(page);
        await page.goto('/app/runs');
        await expect(page).toHaveURL(/\/login$/);
        await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
    });

    test('the login page has no WCAG A/AA axe violations', async ({ page }) => {
        await page.goto('/login');
        await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
        const results = await new AxeBuilder({ page })
            .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
            .analyze();
        expect(results.violations).toEqual([]);
    });

    test('posts the credentials, stores the token and opens the workbench', async ({ page }) => {
        await mockApi(page);
        await page.route('**/api/v1/auth/login', (route) =>
            route.fulfill({
                json: {
                    user: { id: 'u1', email: 'a@example.com' },
                    tokens: { access_token: TOKEN, refresh_token: 'r', token_type: 'Bearer', expires_in: 900 },
                },
            }),
        );
        await page.goto('/app/runs');
        await page.getByLabel('Email').fill('a@example.com');
        await page.getByLabel('Password').fill('correct horse battery');
        const request = page.waitForRequest((r) => r.url().endsWith('/api/v1/auth/login'));
        await page.getByRole('button', { name: 'Sign in' }).click();

        expect((await request).postDataJSON()).toEqual({
            email: 'a@example.com',
            password: 'correct horse battery',
        });
        await expect(page).toHaveURL(/\/app\/runs$/);
        expect(await page.evaluate(() => localStorage.getItem('cerebro.access_token'))).toBe(TOKEN);
    });

    test('shows the failure and stores nothing when the server rejects the credentials', async ({ page }) => {
        await mockApi(page);
        await page.route('**/api/v1/auth/login', (route) =>
            route.fulfill({ status: 401, json: { detail: 'Invalid email or password' } }),
        );
        await page.goto('/login');
        await page.getByLabel('Email').fill('a@example.com');
        await page.getByLabel('Password').fill('wrong password here');
        await page.getByRole('button', { name: 'Sign in' }).click();

        await expect(page.getByRole('alert')).toContainText('Invalid email or password');
        await expect(page).toHaveURL(/\/login$/);
        expect(await page.evaluate(() => localStorage.getItem('cerebro.access_token'))).toBeNull();
    });
});

test.describe('pages show no invented data', () => {
    const fixedFigures = ['124.50', '99.9%', '98%', '92%', 'Math Reasoning', 'Data Extraction', 'RES-104', '$80.00', '$44.50'];

    for (const route of ['/app/dashboard', '/app/qa', '/app/costs', '/app/benchmarks']) {
        test(`${route} states that no data is available`, async ({ page }) => {
            await mockApi(page);
            await page.goto(route);
            await expect(page.getByText('Data unavailable')).toBeVisible();
            const body = await page.locator('main').innerText();
            for (const figure of fixedFigures) expect(body).not.toContain(figure);
        });
    }

    test('settings has no provider key field', async ({ page }) => {
        await mockApi(page);
        await page.goto('/app/settings');
        await expect(page.getByRole('heading', { name: 'System Settings' })).toBeVisible();
        await expect(page.getByLabel(/api key/i)).toHaveCount(0);
        await expect(page.getByText(/api key/i)).toHaveCount(0);
    });

    test('navigation hides memory and logs', async ({ page }) => {
        await mockApi(page);
        await page.goto('/app/workflows');
        const nav = page.getByRole('navigation', { name: 'Legacy and developer navigation' });
        await expect(nav.getByRole('link', { name: 'Research' })).toBeVisible();
        await expect(page.getByRole('link', { name: /^memory$/i })).toHaveCount(0);
        await expect(page.getByRole('link', { name: /logs/i })).toHaveCount(0);
    });
});
