import { expect, type Page, test } from '@playwright/test';

const projects = [
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

async function mockApi(page: Page) {
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

        await route.fulfill({ json: [] });
    });
}

test.describe('accessibility and keyboard navigation', () => {
    test.beforeEach(async ({ page }) => {
        await mockApi(page);
    });

    test('exposes named navigation landmarks and icon-only controls', async ({ page }) => {
        await page.goto('/app/dashboard');

        await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible();
        await expect(page.getByRole('main')).toBeVisible();
        await expect(page.getByRole('button', { name: 'View notifications' })).toBeVisible();
        await expect(page.getByRole('button', { name: 'Toggle color theme' }).first()).toBeVisible();
    });

    test('opens and closes the research dialog from the keyboard', async ({ page }) => {
        await page.goto('/app/research');

        const newResearchButton = page.getByRole('button', { name: 'New Research' });
        await newResearchButton.focus();
        await page.keyboard.press('Enter');

        const dialog = page.getByRole('dialog', { name: 'Create Research Project' });
        await expect(dialog).toBeVisible();
        await expect(page.getByLabel('Title')).toBeVisible();
        await expect(page.getByLabel('Topic / Objective')).toBeVisible();

        await page.keyboard.press('Escape');
        await expect(dialog).toBeHidden();
    });

    test('moves through research detail tabs with arrow keys', async ({ page }) => {
        await page.goto('/app/research/RES-101');

        const overviewTab = page.getByRole('tab', { name: 'Overview' });
        const agentsTab = page.getByRole('tab', { name: 'Agents' });

        await overviewTab.focus();
        await page.keyboard.press('ArrowRight');

        await expect(agentsTab).toHaveAttribute('data-state', 'active');
    });

    test('opens mobile navigation as a named dialog', async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.goto('/app/dashboard');

        await page.getByRole('button', { name: 'Open navigation menu' }).click();

        await expect(page.getByRole('dialog', { name: 'Navigation menu' })).toBeVisible();
        await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible();
    });
});
