import AxeBuilder from '@axe-core/playwright';
import { test, expect } from '@playwright/test';
import { mockApi } from './helpers/mock-api';

const SIGNED_OUT = { cookies: [], origins: [] };
const TOKEN = 'header.payload.sig';
const VALID_PASSWORD = 'CorrectHorse1!';

function errorEnvelope(code: string, message: string, details: Record<string, unknown> = {}) {
    return { error: { code, message, details } };
}

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
        await page.goto('/app/runs?filter=active#top');
        await page.getByLabel('Email').fill('a@example.com');
        await page.getByLabel('Password').fill(VALID_PASSWORD);
        const request = page.waitForRequest((r) => r.url().endsWith('/api/v1/auth/login'));
        await page.getByRole('button', { name: 'Sign in' }).click();

        expect((await request).postDataJSON()).toEqual({
            email: 'a@example.com',
            password: VALID_PASSWORD,
        });
        await expect(page).toHaveURL(/\/app\/runs\?filter=active#top$/);
        expect(await page.evaluate(() => localStorage.getItem('cerebro.access_token'))).toBe(TOKEN);
    });

    test('submits an authenticator code proactively when filled before first submit', async ({ page }) => {
        await mockApi(page);
        let attempts = 0;
        await page.route('**/api/v1/auth/login', (route) => {
            attempts += 1;
            return route.fulfill({
                json: {
                    user: { id: 'u1', email: 'mfa@example.com' },
                    tokens: { access_token: TOKEN, refresh_token: 'r', token_type: 'Bearer', expires_in: 900 },
                },
            });
        });
        await page.goto('/app/runs');
        await page.getByLabel('Email').fill('mfa@example.com');
        await page.getByLabel('Password').fill(VALID_PASSWORD);
        await page.getByLabel(/Authenticator or recovery code/i).fill(' 123456 ');
        const request = page.waitForRequest((r) => r.url().endsWith('/api/v1/auth/login'));
        await page.getByRole('button', { name: 'Sign in' }).click();

        expect((await request).postDataJSON()).toEqual({
            email: 'mfa@example.com',
            password: VALID_PASSWORD,
            totp_code: '123456',
        });
        expect(attempts).toBe(1);
        await expect(page).toHaveURL(/\/app\/runs$/);
        expect(await page.evaluate(() => localStorage.getItem('cerebro.access_token'))).toBe(TOKEN);
    });

    test('prompts for MFA on a 401 challenge without losing credentials and accepts a recovery code', async ({ page }) => {
        await mockApi(page);
        const payloads: Array<Record<string, unknown>> = [];
        await page.route('**/api/v1/auth/login', (route) => {
            const body = route.request().postDataJSON() as Record<string, unknown>;
            payloads.push(body);
            if (!body.totp_code) {
                return route.fulfill({
                    status: 401,
                    json: errorEnvelope('AUTHENTICATION_REQUIRED', 'A valid MFA code is required'),
                });
            }
            return route.fulfill({
                json: {
                    user: { id: 'u1', email: 'mfa@example.com' },
                    tokens: { access_token: TOKEN, refresh_token: 'r', token_type: 'Bearer', expires_in: 900 },
                },
            });
        });

        await page.goto('/app/runs');
        await page.getByLabel('Email').fill('mfa@example.com');
        await page.getByLabel('Password').fill(VALID_PASSWORD);
        await page.getByRole('button', { name: 'Sign in' }).click();

        await expect(page.getByRole('alert')).toContainText('A valid MFA code is required');
        await expect(page.getByLabel('Email')).toHaveValue('mfa@example.com');
        await expect(page.getByLabel('Password')).toHaveValue(VALID_PASSWORD);
        const totpInput = page.getByLabel(/Authenticator or recovery code/i);
        await expect(totpInput).toBeFocused();
        await expect(totpInput).toHaveAttribute('required', '');

        await totpInput.fill('abcde-fghij-klmno-pqrst');
        await page.getByRole('button', { name: 'Sign in' }).click();

        await expect(page).toHaveURL(/\/app\/runs$/);
        expect(payloads).toEqual([
            { email: 'mfa@example.com', password: VALID_PASSWORD },
            { email: 'mfa@example.com', password: VALID_PASSWORD, totp_code: 'abcde-fghij-klmno-pqrst' },
        ]);
        expect(await page.evaluate(() => localStorage.getItem('cerebro.access_token'))).toBe(TOKEN);
    });

    test('shows the failure and stores nothing when the server rejects the credentials', async ({ page }) => {
        await mockApi(page);
        await page.route('**/api/v1/auth/login', (route) =>
            route.fulfill({
                status: 401,
                json: errorEnvelope('AUTHENTICATION_REQUIRED', 'Invalid email or password'),
            }),
        );
        await page.goto('/login');
        await page.getByLabel('Email').fill('a@example.com');
        await page.getByLabel('Password').fill(VALID_PASSWORD);
        await page.getByRole('button', { name: 'Sign in' }).click();

        await expect(page.getByRole('alert')).toContainText('Invalid email or password');
        await expect(page).toHaveURL(/\/login$/);
        expect(await page.evaluate(() => localStorage.getItem('cerebro.access_token'))).toBeNull();
    });

    test('shows validation details from the standard 422 error envelope', async ({ page }) => {
        await mockApi(page);
        await page.route('**/api/v1/auth/login', (route) =>
            route.fulfill({
                status: 422,
                json: errorEnvelope('VALIDATION_ERROR', 'Invalid request', {
                    errors: [
                        {
                            msg: 'Value error, Password must contain uppercase, lowercase, digit, and special character',
                        },
                    ],
                }),
            }),
        );
        await page.goto('/login');
        await page.getByLabel('Email').fill('a@example.com');
        await page.getByLabel('Password').fill('weakpassword');
        await page.getByRole('button', { name: 'Sign in' }).click();

        await expect(page.getByRole('alert')).toContainText(
            'Password must contain uppercase, lowercase, digit, and special character',
        );
        expect(await page.evaluate(() => localStorage.getItem('cerebro.access_token'))).toBeNull();
    });
});

test.describe('pages show no invented data', () => {
    const fixedFigures = [
        '124.50',
        '99.9%',
        '98%',
        '92%',
        'Math Reasoning',
        'Data Extraction',
        'RES-104',
        '$80.00',
        '$44.50',
        '1,204',
        '3,492',
        '42 MB',
    ];

    for (const route of ['/app/dashboard', '/app/memory', '/app/qa', '/app/costs', '/app/benchmarks']) {
        test(`${route} states that no data is available`, async ({ page }) => {
            await mockApi(page);
            let memoryRequested = false;
            page.on('request', (r) => {
                if (r.url().includes('/memory/nodes')) memoryRequested = true;
            });
            await page.goto(route);
            await expect(page.getByText('Data unavailable')).toBeVisible();
            const body = await page.locator('main').innerText();
            for (const figure of fixedFigures) expect(body).not.toContain(figure);
            expect(memoryRequested).toBe(false);
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

test.describe('session expiry, landing link, sign-out and agent logs', () => {
    test('clears the token and redirects to /login on 401 from a handleErrorLocally route', async ({ page }) => {
        await mockApi(page);
        await page.route('**/api/v1/workflows*', (route) =>
            route.fulfill({
                status: 401,
                json: errorEnvelope('AUTHENTICATION_REQUIRED', 'Invalid authentication token'),
            }),
        );
        await page.goto('/app/workflows');
        await expect(page).toHaveURL(/\/login$/);
        expect(await page.evaluate(() => localStorage.getItem('cerebro.access_token'))).toBeNull();
    });

    test('clears the token and redirects to /login on 401 from a standard API route', async ({ page }) => {
        await mockApi(page);
        await page.route('**/api/v1/research/projects*', (route) =>
            route.fulfill({
                status: 401,
                json: errorEnvelope('AUTHENTICATION_REQUIRED', 'Invalid authentication token'),
            }),
        );
        await page.goto('/app/research');
        await expect(page).toHaveURL(/\/login$/);
        expect(await page.evaluate(() => localStorage.getItem('cerebro.access_token'))).toBeNull();
    });

    test('the landing page links to the login page', async ({ page }) => {
        await page.goto('/');
        await page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: 'Sign in' }).click();
        await expect(page).toHaveURL(/\/login$/);
    });

    test('sign out revokes the token, clears local storage and returns to the login page', async ({ page }) => {
        await mockApi(page);
        let logoutAuthHeader: string | undefined;
        await page.route('**/api/v1/auth/logout', (route) => {
            logoutAuthHeader = route.request().headers()['authorization'];
            return route.fulfill({ status: 204, body: '' });
        });
        await page.goto('/app/runs');
        await page.getByRole('button', { name: 'Sign out' }).click();
        await expect(page).toHaveURL(/\/login$/);
        expect(logoutAuthHeader).toMatch(/^Bearer /);
        expect(await page.evaluate(() => localStorage.getItem('cerebro.access_token'))).toBeNull();
        await page.goto('/app/runs');
        await expect(page).toHaveURL(/\/login$/);
    });

    test('sign out clears cached queries before another user signs in', async ({ page }) => {
        await mockApi(page);
        let activeUser = 'user-a';

        await page.route('**/api/v1/auth/logout', (route) =>
            route.fulfill({ status: 204, body: '' }),
        );
        await page.route('**/api/v1/auth/login', (route) =>
            route.fulfill({
                json: {
                    user: { id: 'u2', email: 'b@example.com' },
                    tokens: { access_token: 'user.b.token', refresh_token: 'r', token_type: 'Bearer', expires_in: 900 },
                },
            }),
        );
        await page.route('**/api/v1/research/projects', (route) =>
            route.fulfill({
                json: [
                    {
                        id: activeUser === 'user-a' ? 'RES-A' : 'RES-B',
                        title: activeUser === 'user-a' ? 'User A Confidential Project' : 'User B Project',
                        status: 'running',
                        progress: 10,
                        time: '1m ago',
                        created_at: '2026-05-03T00:00:00Z',
                        query: { text: 'Query' },
                    },
                ],
            }),
        );

        await page.goto('/app/research');
        await expect(page.getByText('User A Confidential Project')).toBeVisible();

        activeUser = 'user-b';
        await page.getByRole('button', { name: 'Sign out' }).click();
        await expect(page).toHaveURL(/\/login$/);

        await page.getByLabel('Email').fill('b@example.com');
        await page.getByLabel('Password').fill(VALID_PASSWORD);
        await page.getByRole('button', { name: 'Sign in' }).click();
        await expect(page).toHaveURL(/\/app\/workflows$/);

        await page
            .getByRole('navigation', { name: 'Legacy and developer navigation' })
            .getByRole('link', { name: 'Research' })
            .click();
        await expect(page.getByText('User B Project')).toBeVisible();
        await expect(page.getByText('User A Confidential Project')).toHaveCount(0);
    });

    test('the agents page shows no log panel and requests no logs', async ({ page }) => {
        await mockApi(page);
        let logRequested = false;
        page.on('request', (r) => {
            if (r.url().includes('/logs')) logRequested = true;
        });
        await page.goto('/app/agents');
        await expect(page.getByText('Researcher Alpha').first()).toBeVisible();
        await expect(page.getByText(/- Logs/)).toHaveCount(0);
        await expect(page.getByLabel('Agent log output')).toHaveCount(0);
        expect(logRequested).toBe(false);
    });
});
