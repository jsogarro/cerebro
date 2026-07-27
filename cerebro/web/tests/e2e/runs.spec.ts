import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { installRunsContractApi } from './helpers/runs-contract-api';

test.describe('runs ledger and lifecycle observation', () => {
  test('scans every lifecycle with truthful metadata and cancellation eligibility', async ({
    page,
  }) => {
    await installRunsContractApi(page);
    await page.goto('/app/runs');

    for (const state of [
      'Pending',
      'Running',
      'Completed with warnings',
      'Failed',
      'Cancelled',
      'Completed',
      'Unknown state',
    ]) {
      await expect(page.getByText(new RegExp(`^${state}`)).filter({ visible: true }).first()).toBeVisible();
    }

    await expect(page.getByText('Fixture', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('version 1.0.0-test').first()).toBeVisible();
    await expect(page.getByText('Not available').first()).toBeVisible();
    await expect(page.getByText('Unavailable').first()).toBeVisible();
    await expect(page.getByText(/1 evaluation record linked/).first()).toBeVisible();
    await expect(page.getByText(/\d+%/)).toHaveCount(0);

    const cancelButtons = page.getByRole('button', { name: 'Cancel run' });
    await expect(cancelButtons).toHaveCount(2);

    for (const objective of [
      'Inspect a completed fixture artifact and its linked evaluation.',
      'Reopen a partial fixture record with an unknown future lifecycle state.',
    ]) {
      await expect(page.getByRole('link', { name: `Open run: ${objective}` })).toHaveAttribute(
        'href',
        /\/app\/runs\/run-/,
      );
    }
  });

  test('shows a deterministic loading state', async ({ page }) => {
    const contractApi = await installRunsContractApi(page, { ledger: 'loading' });
    await page.goto('/app/runs');
    await expect(page.getByRole('heading', { name: 'Loading the run ledger' })).toBeVisible();
    contractApi.releaseLedger();
    await expect(page.getByText(/^Pending/).filter({ visible: true }).first()).toBeVisible();
  });

  test('shows an empty ledger without substituting runs', async ({ page }) => {
    await installRunsContractApi(page, { ledger: 'empty' });
    await page.goto('/app/runs');
    await expect(page.getByRole('heading', { name: 'No run history has been loaded' })).toBeVisible();
    await expect(page.getByRole('link', { name: /Open run/ })).toHaveCount(0);
  });

  test('surfaces a fully malformed ledger', async ({ page }) => {
    await installRunsContractApi(page, { ledger: 'malformed' });
    await page.goto('/app/runs');
    await expect(
      page.getByRole('heading', { name: 'The run ledger response could not be interpreted' }),
    ).toBeVisible();
    await expect(page.getByText(/run_id is missing or invalid/)).toBeVisible();
  });

  test('keeps valid partial records reopenable while identifying malformed peers', async ({
    page,
  }) => {
    await installRunsContractApi(page, { ledger: 'partial' });
    await page.goto('/app/runs');
    await expect(page.getByText('Some run fields could not be interpreted.')).toBeVisible();
    await expect(
      page.getByRole('link', {
        name: 'Open run: Keep this valid run reopenable when another ledger record is malformed.',
      }),
    ).toBeVisible();
  });

  test('offers route-local retry after backend unavailability', async ({ page }) => {
    await installRunsContractApi(page, { ledger: 'unavailable-once' });
    await page.goto('/app/runs');
    await expect(page.getByRole('heading', { name: 'Run ledger is unavailable' })).toBeVisible();
    await expect(page.getByText('No run history has been substituted.')).toBeVisible();
    await expect(page.getByText('API Error')).toHaveCount(0);
    await page.getByRole('button', { name: 'Retry run ledger' }).click();
    await expect(page.getByText(/^Pending/).filter({ visible: true }).first()).toBeVisible();
  });

  test('disables cancellation while pending and updates the row on success', async ({ page }) => {
    const contractApi = await installRunsContractApi(page, {
      cancellation: 'delayed-success',
    });
    await page.goto('/app/runs');

    const pendingRow = page.getByRole('row').filter({
      hasText: 'Await a controlled source pack before beginning fixture research.',
    });
    const runningRow = page.getByRole('row').filter({
      hasText: 'Observe reported task changes for an active fixture research run.',
    });
    await pendingRow.getByRole('button', { name: 'Cancel run' }).click();
    await expect(pendingRow.getByRole('button', { name: 'Cancelling…' })).toBeDisabled();
    await expect(runningRow.getByRole('button', { name: 'Cancel run' })).toBeDisabled();
    contractApi.releaseCancellation();
    await expect(pendingRow.getByText('Cancelled', { exact: true })).toBeVisible();
    await expect(pendingRow.getByRole('button', { name: 'Cancel run' })).toHaveCount(0);
    await expect(pendingRow.getByRole('link', { name: /Open run/ })).toBeVisible();
  });

  test('keeps cancellation failure local and permits retry', async ({ page }) => {
    await installRunsContractApi(page, { cancellation: 'failure' });
    await page.goto('/app/runs');

    const pendingRow = page.getByRole('row').filter({
      hasText: 'Await a controlled source pack before beginning fixture research.',
    });
    await pendingRow.getByRole('button', { name: 'Cancel run' }).click();
    await expect(pendingRow.getByRole('alert')).toContainText(
      'contract fixture rejected cancellation',
    );
    await expect(page.getByText('API Error')).toHaveCount(0);
    await expect(pendingRow.getByRole('button', { name: 'Cancel run' })).toBeEnabled();
    const ids = await page.locator('[id]').evaluateAll((elements) =>
      elements.map((element) => element.id),
    );
    expect(new Set(ids).size).toBe(ids.length);
  });

  test('reports a malformed cancellation success instead of silently accepting it', async ({
    page,
  }) => {
    await installRunsContractApi(page, { cancellation: 'malformed-success' });
    await page.goto('/app/runs');

    const pendingRow = page.getByRole('row').filter({
      hasText: 'Await a controlled source pack before beginning fixture research.',
    });
    await pendingRow.getByRole('button', { name: 'Cancel run' }).click();
    await expect(pendingRow.getByRole('alert')).toContainText(
      'cancellation response did not include an interpretable run',
    );
    await expect(pendingRow.getByRole('button', { name: 'Cancel run' })).toBeEnabled();
  });

  test('does not roll a confirmed cancellation back after a stale ledger read', async ({
    page,
  }) => {
    const contractApi = await installRunsContractApi(page, {
      cancellation: 'stale-ledger-after-success',
    });
    await page.goto('/app/runs');

    const pendingRow = page.getByRole('row').filter({
      hasText: 'Await a controlled source pack before beginning fixture research.',
    });
    await pendingRow.getByRole('button', { name: 'Cancel run' }).click();
    await expect(pendingRow.getByText('Cancelled', { exact: true })).toBeVisible();
    await expect
      .poll(contractApi.getLedgerRequests, { timeout: 5_000 })
      .toBeGreaterThanOrEqual(2);
    await expect(pendingRow.getByText('Cancelled', { exact: true })).toBeVisible();
    await expect(pendingRow.getByRole('button', { name: 'Cancel run' })).toHaveCount(0);
  });

  test('keeps a long cancellation failure associated and within 390px', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await installRunsContractApi(page, { cancellation: 'failure-long-detail' });
    await page.goto('/app/runs');

    const pendingCard = page.getByRole('article', {
      name: 'Run: Await a controlled source pack before beginning fixture research.',
    });
    const cancelButton = pendingCard.getByRole('button', { name: 'Cancel run' });
    await cancelButton.click();

    const alert = pendingCard.getByRole('alert');
    await expect(alert).toContainText('CancellationFailure_');
    const alertId = await alert.getAttribute('id');
    expect(alertId).not.toBeNull();
    await expect(cancelButton).toHaveAttribute('aria-describedby', alertId!);

    const widths = await page.evaluate(() => ({
      document: document.documentElement.scrollWidth,
      viewport: document.documentElement.clientWidth,
    }));
    expect(widths.document).toBeLessThanOrEqual(widths.viewport);
  });

  for (const viewport of [
    { name: 'desktop', width: 1280, height: 900 },
    { name: '390px', width: 390, height: 844 },
  ]) {
    test(`${viewport.name} ledger has no WCAG A/AA axe violations or page overflow`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await installRunsContractApi(page);
      await page.goto('/app/runs');
      await expect(
        page
          .getByRole('link', {
            name: 'Open run: Inspect a completed fixture artifact and its linked evaluation.',
          })
          .filter({ visible: true }),
      ).toBeVisible();

      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze();
      expect(results.violations).toEqual([]);

      const widths = await page.evaluate(() => ({
        document: document.documentElement.scrollWidth,
        viewport: document.documentElement.clientWidth,
      }));
      expect(widths.document).toBeLessThanOrEqual(widths.viewport);

      if (viewport.width === 390) {
        const completedCard = page.getByRole('article', {
          name: 'Run: Inspect a completed fixture artifact and its linked evaluation.',
        });
        await expect(completedCard.getByText('Workflow / version')).toBeVisible();
        await expect(completedCard.getByText('Evaluation result')).toBeVisible();
        await expect(completedCard.getByText('Cost', { exact: true })).toBeVisible();
        await expect(completedCard.getByText('Fixture-derived').first()).toBeVisible();
        await expect(completedCard.getByRole('link', { name: /Open run/ })).toBeVisible();
      }
    });
  }
});
