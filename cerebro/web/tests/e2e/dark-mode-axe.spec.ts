import AxeBuilder from '@axe-core/playwright';
import { expect, type Page, test } from '@playwright/test';
import { installWorkflowsContractApi } from './helpers/workbench-contract-api';
import { installRunDetailContractApi } from './helpers/run-detail-contract-api';

async function enterDark(page: Page) {
  await page.evaluate(() => document.documentElement.classList.add('dark'));
  await expect(page.locator('html')).toHaveClass(/dark/);
}

async function expectAxeClean(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(results.violations).toEqual([]);
}

// The Direction-A palette is AA-verified arithmetically (contrast-report.md);
// these guard that the *merged* app renders the warm dark theme cleanly on each
// redesigned screen — the light passes live in the per-screen specs.
test.describe('dark-mode accessibility on the redesigned screens', () => {
  test('landing has no dark-mode WCAG A/AA axe violations', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await enterDark(page);
    await expectAxeClean(page);
  });

  test('workflow launch has no dark-mode WCAG A/AA axe violations', async ({ page }) => {
    await installWorkflowsContractApi(page);
    await page.goto('/app/workflows');
    await expect(page.getByRole('heading', { name: 'Ready to submit' })).toBeVisible();
    await enterDark(page);
    await expectAxeClean(page);
  });

  test('run trace detail has no dark-mode WCAG A/AA axe violations', async ({ page }) => {
    await installRunDetailContractApi(page, 'completed');
    await page.goto('/app/runs/run-detail-completed');
    await expect(page.getByRole('heading', { name: 'Research artifact', exact: true })).toBeVisible();
    await enterDark(page);
    await expectAxeClean(page);
  });
});
