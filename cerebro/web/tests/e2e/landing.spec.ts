import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

test.describe('Landing page (Direction A)', () => {
  test('states the source-grounded positioning as the single h1', async ({ page }) => {
    await page.goto('/');
    const h1 = page.getByRole('heading', { level: 1 });
    await expect(h1).toHaveCount(1);
    await expect(h1).toContainText('source-grounded AI research workflows');
  });

  test('hero exposes exactly one primary CTA and one secondary CTA', async ({ page }) => {
    await page.goto('/');
    const hero = page.locator('.lp-hero');
    await expect(hero.locator('.lp-btn--primary')).toHaveCount(1);
    await expect(hero.locator('.lp-btn--secondary')).toHaveCount(1);
    await expect(hero.getByRole('link', { name: 'View on GitHub' })).toBeVisible();
    await expect(hero.getByRole('link', { name: 'Read the docs' })).toBeVisible();
  });

  test('CLI panel lists all six research-cli agents subcommands', async ({ page }) => {
    await page.goto('/');
    const cli = page.locator('.lp-cli');
    for (const sub of ['query', 'route', 'estimate', 'execute', 'chain', 'status']) {
      await expect(cli).toContainText(`agents ${sub}`);
    }
  });

  test('renders the six core primitives as inspectable objects', async ({ page }) => {
    await page.goto('/');
    for (const primitive of ['Workflow', 'Run', 'Task', 'Evidence', 'Artifact', 'Evaluator']) {
      await expect(
        page.getByRole('heading', { name: primitive, exact: true }).first(),
      ).toBeVisible();
    }
  });

  test('contains no fabricated marketing content (truthfulness gate)', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('Dr. Alice Chen')).toHaveCount(0);
    await expect(page.getByText('Robert Johnson')).toHaveCount(0);
    await expect(page.getByText('v2.0 Beta now available')).toHaveCount(0);
    await expect(page.getByText('View Demo')).toHaveCount(0);
    await expect(page.getByText('Trusted by Researchers')).toHaveCount(0);
  });

  test('links point at the real repository, not an invented slug', async ({ page }) => {
    await page.goto('/');
    const github = page.getByRole('link', { name: 'View on GitHub' }).first();
    await expect(github).toHaveAttribute('href', 'https://github.com/jsogarro/cerebro');
  });

  for (const theme of ['light', 'dark'] as const) {
    test(`has no WCAG A/AA axe violations (${theme})`, async ({ page }) => {
      await page.goto('/');
      if (theme === 'dark') {
        await page.evaluate(() => document.documentElement.classList.add('dark'));
      }
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze();

      expect(results.violations).toEqual([]);
    });
  }
});
