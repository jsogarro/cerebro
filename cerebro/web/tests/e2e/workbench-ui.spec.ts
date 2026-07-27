import { expect, test } from '@playwright/test';
import { observeRuntime } from './helpers/runtime-observer';
import { installWorkbenchJourneyContractApi } from './helpers/workbench-journey-contract-api';

const viewports = [
  { name: 'desktop', width: 1280, height: 1000 },
  { name: '390px', width: 390, height: 844 },
] as const;

test.describe('research workbench journey', () => {
  for (const viewport of viewports) {
    test(`${viewport.name} discovers, starts, observes, reopens, and inspects a run`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      const runtime = observeRuntime(page);
      const { runId, resourceRequestCounts } =
        await installWorkbenchJourneyContractApi(page);

      await page.goto('/app/workflows');
      const workflow = page.getByRole('article', {
        name: 'Comparative Research Brief',
      });
      await expect(workflow).toContainText('version 1.0.0');
      await expect(page.getByRole('heading', { name: 'Ready to submit' })).toBeVisible();
      await expect(page.getByText('Ready — Approved source pack')).toBeVisible();

      await page.getByRole('button', { name: 'Start controlled run' }).click();
      await expect(page).toHaveURL(new RegExp(`/app/runs/${runId}$`));
      await expect(page.getByText('Pending', { exact: true }).first()).toBeVisible();
      await expect(page.getByText('Running', { exact: true }).first()).toBeVisible({
        timeout: 6_000,
      });
      await expect(page.getByText('Completed', { exact: true }).first()).toBeVisible({
        timeout: 6_000,
      });

      await page.getByRole('button', { name: 'Back to run ledger' }).click();
      const returnedRun = page.getByRole('link', {
        name: `Open run: Assess whether spatiotemporal graph neural networks (ST-GNNs) are viable for predicting corporate revenue, using only the approved source corpus.`,
      });
      await expect(returnedRun).toBeVisible();
      await returnedRun.click();
      await expect(page).toHaveURL(new RegExp(`/app/runs/${runId}$`));

      const artifactHeading = page.getByRole('heading', {
        name: 'An inspectable dossier of invented archive labels',
      });
      const operationsHeading = page.getByRole('heading', {
        name: 'Provider and cost ledger',
      });
      await expect(artifactHeading).toBeVisible();
      for (const resource of ['tasks', 'events', 'evidence', 'artifacts', 'evaluations']) {
        expect(
          resourceRequestCounts.get(resource),
          `${resource} receives a final fetch after the run becomes terminal`,
        ).toBeGreaterThanOrEqual(2);
      }
      expect(await artifactHeading.evaluate((node) => node.getBoundingClientRect().top)).toBeLessThan(
        await operationsHeading.evaluate((node) => node.getBoundingClientRect().top),
      );

      const supportedClaim = page.locator('.run-claim').filter({
        hasText: 'ALPHA belongs to the invented copper collection.',
      });
      await expect(supportedClaim.getByText('Supported', { exact: true })).toBeVisible();
      const citation = supportedClaim.getByRole('button', {
        name: 'Inspect exact evidence 1',
      });
      await citation.focus();
      await page.keyboard.press('Enter');
      const excerpt = page.getByRole('blockquote', {
        name: /Exact evidence excerpt from Invented archive record ALPHA/,
      });
      await expect(excerpt).toBeFocused();
      await expect(excerpt).toContainText(
        'Archive label ALPHA belongs to the invented copper collection.',
      );
      await page.getByRole('button', { name: 'Return to claim' }).click();
      await expect(citation).toBeFocused();

      await expect(page.getByText('fixture-grounding-auditor')).toBeVisible();
      const operations = operationsHeading.locator('xpath=ancestor::section[1]');
      await expect(operations.getByText('contract-fixture-provider')).toBeVisible();
      await expect(operations.getByText('Fixture-derived').first()).toBeVisible();

      if (viewport.width === 390) {
        const widths = await page.evaluate(() => ({
          document: document.documentElement.scrollWidth,
          viewport: document.documentElement.clientWidth,
        }));
        expect(widths.document).toBeLessThanOrEqual(widths.viewport);
      }

      runtime.stop();
      expect(runtime.evidence, 'browser console and network evidence').toEqual({
        consoleErrors: [],
        pageErrors: [],
        requestFailures: [],
        unexpectedHttpFailures: [],
        expectedHttpFailures: [],
      });
    });
  }

  test('permits only the deliberately intercepted workflow outage during retry', async ({
    page,
  }) => {
    const runtime = observeRuntime(page, [
      { method: 'GET', path: '/api/v1/workflows', status: 503 },
    ]);
    let requests = 0;
    await page.route('**/api/v1/workflows', async (route) => {
      requests += 1;
      await route.fulfill({
        status: requests === 1 ? 503 : 200,
        contentType: 'application/json',
        body: JSON.stringify(
          requests === 1
            ? { detail: 'Deliberate retry scenario.' }
            : { items: [] },
        ),
      });
    });

    await page.goto('/app/workflows');
    await expect(page.getByRole('heading', { name: 'Workflow service is unavailable' })).toBeVisible();
    await page.getByRole('button', { name: 'Retry workflow service' }).click();
    await expect(page.getByRole('heading', { name: 'No runnable workflows are available' })).toBeVisible();

    runtime.stop();
    expect(runtime.evidence.consoleErrors).toEqual([
      expect.stringMatching(/Failed to load resource.*503/),
    ]);
    expect(runtime.evidence.pageErrors).toEqual([]);
    expect(runtime.evidence.requestFailures).toEqual([]);
    expect(runtime.evidence.unexpectedHttpFailures).toEqual([]);
    expect(runtime.evidence.expectedHttpFailures).toEqual([
      'GET /api/v1/workflows — 503',
    ]);
  });
});
