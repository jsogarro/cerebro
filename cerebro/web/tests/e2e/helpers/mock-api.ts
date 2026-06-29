import type { Page } from '@playwright/test';

export const projects = [
    {
        id: 'RES-101',
        title: 'Supply Chain Optimization',
        status: 'running',
        progress: 42,
        time: '1d ago',
        created_at: '2026-05-03T00:00:00Z',
        query: { main_query: 'Optimize supplier routing.' },
    },
];

export async function mockApi(page: Page) {
    await page.route('**/api/v1/**', async (route) => {
        const url = new URL(route.request().url());
        const path = url.pathname;

        if (path.endsWith('/research/projects/RES-101')) {
            await route.fulfill({ json: projects[0] });
            return;
        }

        if (path.endsWith('/research/projects')) {
            await route.fulfill({ json: projects });
            return;
        }

        if (path.endsWith('/agents')) {
            await route.fulfill({
                json: [
                    {
                        id: 'A-01',
                        name: 'Researcher Alpha',
                        role: 'Research',
                        status: 'active',
                    },
                ],
            });
            return;
        }

        if (path.endsWith('/agents/A-01/logs')) {
            await route.fulfill({ json: ['INFO Agent ready'] });
            return;
        }

        if (path.endsWith('/memory/nodes')) {
            await route.fulfill({
                json: [
                    {
                        id: 'MEM-101',
                        content: 'Supplier risk threshold updated',
                        type: 'episodic',
                        timestamp: '2026-05-03T00:00:00Z',
                    },
                ],
            });
            return;
        }

        await route.fulfill({ json: [] });
    });
}
