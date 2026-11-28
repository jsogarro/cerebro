import { test, expect } from '@playwright/test';
import { mockApi } from './helpers/mock-api';

const USER_ID = '11111111-2222-3333-4444-555555555555';
const b64url = (value: object) => Buffer.from(JSON.stringify(value)).toString('base64url');
const TOKEN = `${b64url({ alg: 'none' })}.${b64url({ sub: USER_ID })}.sig`;

test.describe('API client contract', () => {
    test.beforeEach(async ({ page }) => {
        await page.addInitScript((token) => {
            window.localStorage.setItem('cerebro.access_token', token);
        }, TOKEN);
    });

    test('sends the bearer token on API requests', async ({ page }) => {
        await mockApi(page);
        const request = page.waitForRequest('**/api/v1/research/projects');
        await page.goto('/app/research');
        expect((await request).headers()['authorization']).toBe(`Bearer ${TOKEN}`);
    });

    test('creates a research project in the shape the server validates', async ({ page }) => {
        await mockApi(page);
        await page.goto('/app/research');
        await page.getByRole('button', { name: 'New Research' }).click();
        await page.getByLabel('Title').fill('Grid study');
        await page.getByLabel('Topic / Objective').fill('How resilient is the grid?');
        const request = page.waitForRequest(
            (req) => req.method() === 'POST' && req.url().endsWith('/api/v1/research/projects'),
        );
        await page.getByRole('button', { name: 'Start Research' }).click();
        expect((await request).postDataJSON()).toMatchObject({
            title: 'Grid study',
            user_id: USER_ID,
            query: { text: 'How resilient is the grid?', domains: ['general'] },
        });
    });

    test('opens the project WebSocket with the token and subscribes by action', async ({ page }) => {
        await mockApi(page);
        const urls: string[] = [];
        const sent: string[] = [];
        await page.routeWebSocket(/\/ws(\?|$)/, (ws) => {
            urls.push(ws.url());
            ws.onMessage((message) => sent.push(String(message)));
        });
        await page.goto('/app/research/RES-101');
        await expect.poll(() => urls.length).toBeGreaterThan(0);
        expect(new URL(urls[0]).searchParams.get('token')).toBe(TOKEN);
        await expect
            .poll(() => sent.map((m) => JSON.parse(m)))
            .toContainEqual({ type: 'subscription', data: { action: 'subscribe', project_id: 'RES-101' } });
    });

    test('renders the server agent list shape', async ({ page }) => {
        await mockApi(page);
        await page.route('**/api/v1/agents', (route) =>
            route.fulfill({
                json: {
                    agents: [{ agent_type: 'literature_review', name: 'Literature Reviewer', description: 'Finds papers' }],
                    total_agents: 1,
                },
            }),
        );
        await page.goto('/app/agents');
        await expect(page.getByText('Literature Reviewer')).toBeVisible();
    });

    test.describe('WebSocket lifecycle', () => {
        test('does not retry when the server rejects the token', async ({ page }) => {
            await mockApi(page);
            let connections = 0;
            await page.routeWebSocket(/\/ws(\?|$)/, (ws) => {
                connections += 1;
                ws.close({ code: 1008, reason: 'bad token' });
            });
            await page.goto('/app/research/RES-101');
            await page.waitForTimeout(500);
            const afterFirstAttempt = connections; // React StrictMode mounts twice in dev
            await page.waitForTimeout(3500);
            expect(connections).toBe(afterFirstAttempt);
        });

        test('re-sends the subscription after a reconnect', async ({ page }) => {
            await mockApi(page);
            const subscribes: string[] = [];
            let connections = 0;
            await page.routeWebSocket(/\/ws(\?|$)/, (ws) => {
                connections += 1;
                ws.onMessage((message) => subscribes.push(String(message)));
                if (connections === 1) ws.close({ code: 1011 });
            });
            await page.goto('/app/research/RES-101');
            await expect.poll(() => subscribes.length, { timeout: 8000 }).toBeGreaterThan(0);
            expect(connections).toBe(2);
            expect(JSON.parse(subscribes[0]).data).toEqual({ action: 'subscribe', project_id: 'RES-101' });
        });

        test('does not reconnect after leaving the screen', async ({ page }) => {
            await mockApi(page);
            let connections = 0;
            await page.routeWebSocket(/\/ws(\?|$)/, (ws) => {
                connections += 1;
                setTimeout(() => ws.close({ code: 1011 }), 300);
            });
            await page.goto('/app/research/RES-101');
            await expect.poll(() => connections).toBeGreaterThan(0);
            await page.waitForTimeout(700); // after the server close, while the reconnect timer is pending
            await page.getByRole('link', { name: /agents/i }).first().click();
            await page.waitForTimeout(500);
            const afterLeaving = connections;
            await page.waitForTimeout(3000);
            expect(connections).toBe(afterLeaving);
        });

        test('shows progress for this project only', async ({ page }) => {
            await mockApi(page);
            await page.routeWebSocket(/\/ws(\?|$)/, (ws) => {
                ws.onMessage(() => {
                    const frame = (project: string, pct: number) =>
                        JSON.stringify({ type: 'progress', project_id: project, data: { progress_percentage: pct } });
                    ws.send(frame('RES-101', 55));
                    ws.send(frame('OTHER', 77));
                });
            });
            await page.goto('/app/research/RES-101');
            await expect(page.getByText('55%').first()).toBeVisible();
            await page.waitForTimeout(500);
            await expect(page.getByText('55%').first()).toBeVisible();
            await expect(page.getByText('77%')).toHaveCount(0);
        });
    });

    test('refuses to create a project without a token', async ({ page }) => {
        await mockApi(page);
        let posted = false;
        page.on('request', (req) => {
            if (req.method() === 'POST') posted = true;
        });
        await page.goto('/app/research');
        // The route guard keeps a signed-out visitor out, so the token is lost after the page loads.
        await page.evaluate(() => window.localStorage.removeItem('cerebro.access_token'));
        await page.getByRole('button', { name: 'New Research' }).click();
        await page.getByLabel('Title').fill('No token');
        await page.getByRole('button', { name: 'Start Research' }).click();
        await expect(page.getByText('Sign in required', { exact: true })).toBeVisible();
        expect(posted).toBe(false);
    });
});
