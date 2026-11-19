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
});
