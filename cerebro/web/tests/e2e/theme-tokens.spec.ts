import { expect, test } from '@playwright/test';
import { installWorkflowsContractApi } from './helpers/workbench-contract-api';

function cssVar(page: import('@playwright/test').Page, name: string) {
  return page.evaluate(
    (variable) =>
      getComputedStyle(document.documentElement).getPropertyValue(variable).trim(),
    name,
  );
}

async function readSurfaceBackgrounds(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const cardEl = document.querySelector('[role="article"]');
    return {
      body: getComputedStyle(document.body).backgroundColor,
      card: cardEl ? getComputedStyle(cardEl).backgroundColor : null,
    };
  });
}

test.describe('Direction-A token & font migration', () => {
  test('light defaults expose the warm token palette and the Plex UI font', async ({ page }) => {
    await installWorkflowsContractApi(page);
    await page.goto('/app/workflows');
    await expect(page.getByRole('heading', { name: 'Ready to submit' })).toBeVisible();

    // Canonical Direction-A light canvas value (tokens.css is authoritative, C8).
    expect(await cssVar(page, '--bg-base')).toBe('42 28% 96%');
    // The single signal accent must resolve.
    expect(await cssVar(page, '--signal')).not.toBe('');

    const bodyFont = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
    expect(bodyFont).toContain('IBM Plex Sans');
  });

  test('dark palette activates via the .dark class (not [data-theme]) — guards C1', async ({
    page,
  }) => {
    await installWorkflowsContractApi(page);
    await page.goto('/app/workflows');
    await expect(page.getByRole('heading', { name: 'Ready to submit' })).toBeVisible();

    // Setting [data-theme] alone must NOT flip the palette in this app.
    await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'));
    expect(await cssVar(page, '--bg-base')).toBe('42 28% 96%');

    // The Tailwind .dark class is what carries the warm dark base.
    await page.evaluate(() => document.documentElement.classList.add('dark'));
    expect(await cssVar(page, '--bg-base')).toBe('40 14% 8%');
  });

  test('cards stay distinct from the canvas in both themes (no vanishing card)', async ({
    page,
  }) => {
    await installWorkflowsContractApi(page);
    await page.goto('/app/workflows');
    await expect(
      page.getByRole('article', { name: 'Comparative Research Brief' }).first(),
    ).toBeVisible();

    const light = await readSurfaceBackgrounds(page);
    expect(light.card).not.toBeNull();
    expect(light.card).not.toBe(light.body);

    await page.evaluate(() => document.documentElement.classList.add('dark'));
    const dark = await readSurfaceBackgrounds(page);
    expect(dark.card).not.toBeNull();
    expect(dark.card).not.toBe(dark.body);
    // And the dark canvas must actually be dark, not the retired navy or a light value.
    expect(dark.body).not.toBe(light.body);
  });
});
