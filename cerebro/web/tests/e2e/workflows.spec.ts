import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { installWorkflowsContractApi } from './helpers/workbench-contract-api';

test.describe('workflow discovery and controlled start', () => {
  test('presents catalog outcome, version, requirements, limits, and unavailable values', async ({
    page,
  }) => {
    await installWorkflowsContractApi(page);
    await page.goto('/app/workflows');

    const workflow = page.getByRole('article', { name: 'Comparative Research Brief' });
    await expect(workflow).toContainText('version 1.0.0');
    await expect(workflow).toContainText('Experimental');
    await expect(workflow).toContainText('Fixture');
    await expect(workflow).toContainText('Input: objective');
    await expect(workflow).toContainText('Research Brief');
    await expect(workflow).toContainText('No catalog result reported');
    await expect(workflow).toContainText('Not available');

    await expect(page.getByRole('heading', { name: 'Start a fixture-backed research run' })).toBeVisible();
    await expect(page.getByLabel('Fixed objective')).toHaveValue(/ST-GNNs/);
    await expect(page.getByLabel('Approved source pack')).toHaveValue(
      'golden-run.corporate-revenue-stgnn-viability.v1',
    );
    await expect(page.getByRole('heading', { name: 'Ready to submit' })).toBeVisible();
    await expect(page.getByText('Ready — Fixture execution')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start controlled run' })).toBeEnabled();
  });

  test('blocks preflight when the selected workflow does not support fixture execution', async ({
    page,
  }) => {
    await installWorkflowsContractApi(page, { catalog: 'blocked' });
    await page.goto('/app/workflows');

    await expect(page.getByRole('heading', { name: 'Start is blocked' })).toBeVisible();
    await expect(page.getByText('Blocked — Fixture execution')).toBeVisible();
    await expect(page.getByText('does not report fixture support')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Resolve blocked checks' })).toBeDisabled();
  });

  test('blocks the pinned corpus for a different immutable workflow version', async ({ page }) => {
    await installWorkflowsContractApi(page, { catalog: 'incompatible-fixture' });
    await page.goto('/app/workflows');

    const priorVersion = page.getByRole('article', {
      name: 'Comparative Research Brief — prior fixture version',
    });
    await priorVersion.getByRole('button', { name: 'Configure controlled run' }).click();
    await expect(priorVersion.getByRole('button', { name: 'Selected for preflight' })).toBeVisible();
    await expect(
      page
        .getByRole('article', { name: 'Comparative Research Brief', exact: true })
        .getByRole('button', { name: 'Configure controlled run' }),
    ).toBeVisible();
    await expect(page.getByText('Blocked — Canonical workflow version')).toBeVisible();
    await expect(page.getByText(/requires comparative-research\.corporate-revenue-stgnn@1\.0\.0/)).toBeVisible();
    await expect(page.getByText('Blocked — Approved source pack')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Resolve blocked checks' })).toBeDisabled();
  });

  test('offers local retry when the workflow service is unavailable without substituting data', async ({
    page,
  }) => {
    await installWorkflowsContractApi(page, { catalog: 'unavailable-once' });
    await page.goto('/app/workflows');

    await expect(page.getByRole('heading', { name: 'Workflow service is unavailable' })).toBeVisible();
    await expect(page.getByText('No workflow or run data has been substituted.')).toBeVisible();
    await expect(page.getByText('API Error')).toHaveCount(0);

    await page.getByRole('button', { name: 'Retry workflow service' }).click();
    await expect(page.getByRole('heading', { name: 'Comparative Research Brief' })).toBeVisible();
  });

  test('shows submission progress and navigates only to the returned run identifier', async ({
    page,
  }) => {
    const contractApi = await installWorkflowsContractApi(page, {
      createRun: 'delayed-success',
    });
    await page.goto('/app/workflows');

    const createRequest = page.waitForRequest(
      (request) =>
        request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/runs',
    );
    await page.getByRole('button', { name: 'Start controlled run' }).click();
    const request = await createRequest;
    await expect(page.getByRole('button', { name: 'Submitting controlled run…' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Selected for preflight' })).toBeDisabled();
    await expect(page.getByLabel('Time limit')).toBeDisabled();
    await expect(page.getByLabel('Cost limit')).toBeDisabled();
    expect(request.postDataJSON()).toEqual({
      workflow_id: 'comparative-research.corporate-revenue-stgnn',
      workflow_version: '1.0.0',
      objective:
        'Assess whether spatiotemporal graph neural networks (ST-GNNs) are viable for predicting corporate revenue, using only the approved source corpus.',
      inputs: {
        source_pack_id: 'golden-run.corporate-revenue-stgnn-viability.v1',
        source_count: 5,
      },
      mode: 'fixture',
      budget: {
        max_duration_seconds: 600,
        max_cost: 0,
        currency: 'USD',
      },
    });
    contractApi.releaseCreateRun();
    await expect(page).toHaveURL(/\/app\/runs\/run-controlled-fixture-001$/);
  });

  test('does not navigate when run creation omits a normalized run identifier', async ({ page }) => {
    await installWorkflowsContractApi(page, { createRun: 'malformed-success' });
    await page.goto('/app/workflows');

    await page.getByRole('button', { name: 'Start controlled run' }).click();

    await expect(page.getByRole('alert').filter({ hasText: 'Run was not created' })).toBeVisible();
    await expect(page).toHaveURL(/\/app\/workflows$/);
    await expect(page.getByText('API Error')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Start controlled run' })).toBeEnabled();
  });

  test('keeps submission failure in the preflight surface and permits retry', async ({ page }) => {
    await installWorkflowsContractApi(page, { createRun: 'failure' });
    await page.goto('/app/workflows');

    await page.getByRole('button', { name: 'Start controlled run' }).click();
    const alert = page.getByRole('alert', { name: '' }).filter({ hasText: 'Run submission failed' });
    await expect(alert).toContainText('contract fixture rejected this run submission');
    await expect(page.getByText('API Error')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Start controlled run' })).toBeEnabled();
  });

  for (const viewport of [
    { name: 'desktop', width: 1280, height: 900 },
    { name: '390px', width: 390, height: 844 },
  ]) {
    test(`${viewport.name} workflow setup has no WCAG A/AA axe violations`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await installWorkflowsContractApi(page);
      await page.goto('/app/workflows');
      await expect(page.getByRole('heading', { name: 'Ready to submit' })).toBeVisible();

      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze();

      expect(results.violations).toEqual([]);
      if (viewport.width === 390) {
        const widths = await page.evaluate(() => ({
          document: document.documentElement.scrollWidth,
          viewport: document.documentElement.clientWidth,
        }));
        expect(widths.document).toBeLessThanOrEqual(widths.viewport);
      }
    });
  }
});
