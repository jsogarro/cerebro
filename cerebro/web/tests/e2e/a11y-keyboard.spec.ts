import { expect, test } from '@playwright/test';

import { mockApi } from './helpers/mock-api';

test.describe('accessibility and keyboard navigation', () => {
    test.beforeEach(async ({ page }) => {
        await mockApi(page);
    });

    test('exposes named navigation landmarks and icon-only controls', async ({ page }) => {
        await page.goto('/app/workflows');

        await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible();
        await expect(page.getByRole('navigation', { name: 'Legacy and developer navigation' })).toBeVisible();
        await expect(page.getByRole('main')).toBeVisible();
        await expect(page.getByRole('button', { name: 'View notifications' })).toBeVisible();
        await expect(page.getByRole('button', { name: 'Toggle color theme' }).first()).toBeVisible();
    });

    test('defaults to Workflows and keeps the primary journey ordered', async ({ page }) => {
        await page.goto('/app');

        await expect(page).toHaveURL(/\/app\/workflows$/);
        const primaryNavigation = page.getByRole('navigation', { name: 'Primary navigation' });
        await expect(primaryNavigation.getByRole('link')).toHaveText([/Workflows/, /Runs/]);
        await expect(primaryNavigation.getByRole('link', { name: 'Workflows' })).toHaveAttribute('aria-current', 'page');
    });

    const routeHeadings = [
        ['/app/workflows', 'Workflows'],
        ['/app/runs', 'Runs'],
        ['/app/dashboard', 'Dashboard'],
        ['/app/research', 'Research'],
        ['/app/research/RES-101', 'Research project'],
        ['/app/agents', 'Agents'],
        ['/app/memory', 'Memory'],
        ['/app/qa', 'Quality assurance'],
        ['/app/costs', 'Costs'],
        ['/app/benchmarks', 'Benchmarks'],
        ['/app/settings', 'Settings'],
    ] as const;

    for (const [route, heading] of routeHeadings) {
        test(`${route} exposes one route-aware h1 and one main landmark`, async ({ page }) => {
            await page.goto(route);

            await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
            await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
            await expect(page.getByRole('main')).toHaveCount(1);
        });
    }

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

    test('contains mobile focus and returns it after Escape and the close control', async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.goto('/app/workflows');

        const trigger = page.getByRole('button', { name: 'Open navigation menu' });
        await trigger.click();

        const dialog = page.getByRole('dialog', { name: 'Navigation menu' });
        const firstLink = dialog.getByRole('link', { name: 'Workflows' });
        await expect(dialog).toBeVisible();
        await expect(firstLink).toBeFocused();

        await page.keyboard.press('Shift+Tab');
        expect(await dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);

        await page.keyboard.press('Escape');
        await expect(dialog).toBeHidden();
        await expect(trigger).toBeFocused();

        await trigger.click();
        await dialog.getByRole('button', { name: 'Close dialog' }).click();
        await expect(dialog).toBeHidden();
        await expect(trigger).toBeFocused();
    });

    test('closes mobile navigation after selecting a destination', async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.goto('/app/workflows');

        await page.getByRole('button', { name: 'Open navigation menu' }).click();
        const dialog = page.getByRole('dialog', { name: 'Navigation menu' });
        await dialog.getByRole('link', { name: 'Runs' }).click();

        await expect(page).toHaveURL(/\/app\/runs$/);
        await expect(dialog).toBeHidden();
    });
});
