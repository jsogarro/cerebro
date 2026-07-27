import { expect, type Page, test } from '@playwright/test';
import { installWorkflowsContractApi } from './helpers/workbench-contract-api';
import { installRunDetailContractApi } from './helpers/run-detail-contract-api';

// Records a light→dark walkthrough of each redesigned screen at three widths
// (video artifacts land in test-results/) and doubles as a responsive-render
// guard: the primary heading stays visible and the page never scrolls
// horizontally in either theme.
test.use({ video: 'on' });

const viewports = [
  { name: 'desktop', width: 1280, height: 900 },
  { name: 'tablet', width: 834, height: 1112 },
  { name: 'mobile', width: 390, height: 844 },
];

async function assertNoHorizontalOverflow(page: Page) {
  const widths = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth,
    viewport: document.documentElement.clientWidth,
  }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport);
}

async function walkThemes(page: Page, headingVisible: () => Promise<void>) {
  await headingVisible();
  await assertNoHorizontalOverflow(page);
  await page.evaluate(() => document.documentElement.classList.add('dark'));
  await expect(page.locator('html')).toHaveClass(/dark/);
  await headingVisible();
  await assertNoHorizontalOverflow(page);
}

for (const viewport of viewports) {
  test.describe(`${viewport.name} walkthrough`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    test('landing', async ({ page }) => {
      await page.goto('/');
      await walkThemes(page, async () => {
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      });
    });

    test('workflow launch', async ({ page }) => {
      await installWorkflowsContractApi(page);
      await page.goto('/app/workflows');
      await walkThemes(page, async () => {
        await expect(
          page.getByRole('heading', { name: 'Workflow catalog', exact: true }),
        ).toBeVisible();
      });
    });

    test('run trace detail', async ({ page }) => {
      await installRunDetailContractApi(page, 'completed');
      await page.goto('/app/runs/run-detail-completed');
      await walkThemes(page, async () => {
        await expect(
          page.getByRole('heading', { name: 'Research artifact', exact: true }),
        ).toBeVisible();
      });
    });
  });
}
