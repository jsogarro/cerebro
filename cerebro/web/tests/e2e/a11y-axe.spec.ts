import AxeBuilder from '@axe-core/playwright';
import { expect, type Page, test } from '@playwright/test';

import { mockApi } from './helpers/mock-api';

const appRoutes = [
    '/app/dashboard',
    '/app/research',
    '/app/research/RES-101',
    '/app/agents',
    '/app/memory',
    '/app/settings',
];

test.describe('axe accessibility checks', () => {
    test.beforeEach(async ({ page }) => {
        await mockApi(page);
    });

    async function waitForSettledPage(page: Page) {
        await page.waitForTimeout(650);
    }

    for (const route of appRoutes) {
        test(`${route} has no WCAG A/AA axe violations`, async ({ page }) => {
            await page.goto(route);
            await expect(page.getByRole('main')).toBeVisible();
            await waitForSettledPage(page);

            const results = await new AxeBuilder({ page })
                .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
                .analyze();

            expect(results.violations).toEqual([]);
        });
    }

    test('mobile navigation dialog has no WCAG A/AA axe violations', async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.goto('/app/dashboard');
        await page.getByRole('button', { name: 'Open navigation menu' }).click();
        await waitForSettledPage(page);

        const results = await new AxeBuilder({ page })
            .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
            .analyze();

        expect(results.violations).toEqual([]);
    });
});
