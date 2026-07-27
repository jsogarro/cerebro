import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { installRunDetailContractApi, type RunDetailScenario } from './helpers/run-detail-contract-api';

const routeFor = (scenario: RunDetailScenario) => `/app/runs/run-detail-${scenario}`;

test.describe('inspectable run detail', () => {
  test('makes the structured artifact the primary reading surface and keeps fixture context persistent', async ({ page }) => {
    await installRunDetailContractApi(page, 'completed');
    await page.goto(routeFor('completed'));

    const overview = page.getByRole('heading', { name: 'Run overview and configuration' });
    const artifact = page.getByRole('heading', { name: 'Research artifact', exact: true });
    await expect(overview).toBeVisible();
    await expect(artifact).toBeVisible();
    expect(await overview.evaluate((node) => node.getBoundingClientRect().top)).toBeLessThan(
      await artifact.evaluate((node) => node.getBoundingClientRect().top),
    );
    await expect(page.getByRole('heading', { name: 'An inspectable dossier of invented archive labels' })).toBeVisible();
    await expect(page.getByText('Controlled fixture context')).toHaveCount(2);
    await expect(page.getByText('Claims and support')).toBeVisible();
    for (const status of ['Supported', 'Partially supported', 'Disputed', 'Unsupported']) {
      await expect(page.getByText(status, { exact: true })).toBeVisible();
    }
  });

  test('moves focus to the exact cited excerpt, announces transfer, and returns coherently', async ({ page }) => {
    await installRunDetailContractApi(page, 'completed');
    await page.goto(routeFor('completed'));

    const supportedClaim = page.locator('.run-claim').filter({
      hasText: 'ALPHA belongs to the invented copper collection.',
    });
    const citation = supportedClaim.getByRole('button', { name: 'Inspect exact evidence 1' });
    await citation.focus();
    await page.keyboard.press('Enter');

    const excerpt = page.getByRole('blockquote', {
      name: /Exact evidence excerpt from Invented archive record ALPHA/,
    });
    await expect(excerpt).toBeFocused();
    await expect(excerpt).toContainText('Archive label ALPHA belongs to the invented copper collection.');
    await expect(page.locator('[aria-live="polite"]')).toContainText('Focused exact evidence excerpt');
    await expect(excerpt.locator('xpath=ancestor::article[1]')).toHaveClass(/run-evidence-record-selected/);

    await page.getByRole('button', { name: 'Return to claim' }).click();
    await expect(citation).toBeFocused();
    await expect(page.locator('[aria-live="polite"]')).toContainText('Returned to claim');
  });

  test('explains unsupported and absent excerpts without linking nowhere', async ({ page }) => {
    await installRunDetailContractApi(page, 'completed');
    await page.goto(routeFor('completed'));

    const unsupported = page.locator('.run-claim').filter({
      hasText: 'GAMMA belongs to an invented collection.',
    });
    await expect(unsupported).toContainText('No fixture record or excerpt for GAMMA was supplied.');
    await expect(unsupported).toContainText('No evidence is linked; there is no citation target to inspect.');
    await expect(unsupported.getByRole('button', { name: /Inspect exact evidence/ })).toHaveCount(0);

    const partial = page.locator('.run-claim').filter({
      hasText: 'ALPHA and BETA both belong to named invented collections.',
    });
    await expect(partial).toContainText('1 linked evidence record cannot provide an exact excerpt.');
  });

  test('does not hide valid subresources when optional run summary references are omitted', async ({
    page,
  }) => {
    await installRunDetailContractApi(page, 'omitted-summary-references');
    await page.goto(routeFor('omitted-summary-references'));

    await expect(
      page.getByRole('heading', {
        name: 'An inspectable dossier of invented archive labels',
      }),
    ).toBeVisible();
    await expect(page.getByText('fixture-grounding-auditor')).toBeVisible();
    await expect(
      page.getByText('Some artifact records failed run-integrity checks.'),
    ).toHaveCount(0);
  });

  test('quarantines subresources when reported summary references are malformed', async ({
    page,
  }) => {
    await installRunDetailContractApi(page, 'malformed-summary-references');
    await page.goto(routeFor('malformed-summary-references'));

    await expect(
      page.getByRole('heading', { name: 'An inspectable dossier of invented archive labels' }),
    ).toHaveCount(0);
    await expect(page.getByText('fixture-grounding-auditor')).toHaveCount(0);
    await expect(page.getByText(/artifact_ids contains one or more malformed references/)).toBeVisible();
    await expect(page.getByText(/evaluation_ids contains one or more malformed references/)).toBeVisible();
  });

  test('downgrades positive support when a referenced evidence record is missing', async ({
    page,
  }) => {
    await installRunDetailContractApi(page, 'missing-claim-evidence');
    await page.goto(routeFor('missing-claim-evidence'));

    const supportedClaim = page.locator('.run-claim').filter({
      hasText: 'ALPHA belongs to the invented copper collection.',
    });
    await expect(supportedClaim.getByText(/Unknown support/)).toBeVisible();
    await expect(supportedClaim).toContainText(
      'reported support cannot be independently verified',
    );
    await expect(
      page.getByText(/Claim claim-detail-supported references missing evidence evidence-detail-alpha/),
    ).toBeVisible();
  });

  test('downgrades supported claims whose referenced evidence is unavailable', async ({
    page,
  }) => {
    await installRunDetailContractApi(page, 'unavailable-supported-evidence');
    await page.goto(routeFor('unavailable-supported-evidence'));

    const supportedClaim = page.locator('.run-claim').filter({
      hasText: 'ALPHA belongs to the invented copper collection.',
    });
    await expect(supportedClaim.getByText(/Unknown support/)).toBeVisible();
    await expect(supportedClaim).toContainText(
      'not available for independent verification',
    );
    await expect(
      page.getByText(/Claim claim-detail-supported references evidence evidence-detail-beta, but its availability is unavailable/),
    ).toBeVisible();
  });

  test('keeps returned evidence independently inspectable when no artifact can be rendered', async ({ page }) => {
    await installRunDetailContractApi(page, 'evidence-only');
    await page.goto(routeFor('evidence-only'));
    await expect(page.getByRole('heading', { name: 'No artifacts have been recorded' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Exact evidence' })).toBeVisible();
    await expect(page.getByRole('blockquote', {
      name: /Exact evidence excerpt from Invented archive record ALPHA/,
    })).toBeVisible();
  });

  for (const scenario of ['evidence-unavailable', 'evidence-malformed'] as const) {
    test(`does not recast ${scenario.replace('evidence-', '')} evidence as authoritative absence`, async ({ page }) => {
      await installRunDetailContractApi(page, scenario);
      await page.goto(routeFor(scenario));
      await expect(page.getByRole('heading', { name: 'An inspectable dossier of invented archive labels' })).toBeVisible();
      await expect(page.getByText(`Evidence inspection is ${scenario === 'evidence-unavailable' ? 'unavailable' : 'malformed'}; citation targets cannot be resolved yet.`).first()).toBeVisible();
      await expect(page.getByText('No evidence records were returned.')).toHaveCount(0);
      await expect(page.getByText('No evidence is linked; there is no citation target to inspect.')).toHaveCount(0);
      await expect(page.getByText(/references missing evidence/)).toHaveCount(0);
      await expect(page.getByText(/reported support cannot be independently verified/)).toHaveCount(0);
    });
  }

  test('keeps evidence loading distinct until the contract resolves', async ({ page }) => {
    const api = await installRunDetailContractApi(page, 'evidence-loading');
    await page.goto(routeFor('evidence-loading'));
    await expect(page.getByText('Evidence inspection is loading; citation targets cannot be resolved yet.').first()).toBeVisible();
    await expect(page.getByText('No evidence records were returned.')).toHaveCount(0);
    api.releaseEvidence();
    await expect(page.getByRole('button', { name: 'Inspect exact evidence 1' }).first()).toBeVisible();
  });

  test('renders the ordered task/event research log with attempts, degradation, failure detail, and collapsed traces', async ({ page }) => {
    await installRunDetailContractApi(page, 'failed');
    await page.goto(routeFor('failed'));

    await expect(page.getByRole('heading', { name: 'Ordered research log' })).toBeVisible();
    await expect(page.getByText('Attempt 3')).toBeVisible();
    await expect(page.getByText('The test-only validator rejected a deliberately malformed fixture record.')).toBeVisible();
    await expect(page.getByText('Controlled fixture execution started.')).toBeVisible();
    const traceDetail = page.getByText('Trace and event metadata').first();
    await expect(traceDetail).toBeVisible();
    expect(await traceDetail.evaluate((node) => (node.parentElement as HTMLDetailsElement).open)).toBe(false);
    await expect(page.getByText('trace-test-only-detail').first()).toBeHidden();
  });

  test('shows evaluation identity, type, version, severity, explanation, references, and operational origins', async ({ page }) => {
    await installRunDetailContractApi(page, 'completed');
    await page.goto(routeFor('completed'));

    await expect(page.getByText('fixture-grounding-auditor')).toBeVisible();
    await expect(page.getByText('vtest-v5')).toBeVisible();
    await expect(page.getByText('Evaluator · deterministic')).toBeVisible();
    await expect(page.getByText('warning · severity warning')).toBeVisible();
    await expect(page.getByText('Supported claims have exact excerpts; the BETA evidence gap remains explicit.')).toBeVisible();
    const evaluationRecord = page.getByText('fixture-grounding-auditor').locator('xpath=ancestor::article[1]');
    await expect(evaluationRecord.getByText('artifact-detail-fixture')).toBeVisible();

    const ledger = page.getByRole('heading', { name: 'Provider and cost ledger' }).locator('xpath=ancestor::section[1]');
    await expect(ledger.getByText('contract-fixture-provider')).toBeVisible();
    await expect(ledger.getByText('fixture-research-synthesis')).toBeVisible();
    await expect(ledger.locator('.workbench-provenance-fixture')).toHaveCount(10);
    await expect(page.getByText('Prompts, policy, and developer metadata')).toBeVisible();
    await expect(page.getByText(/TEST ONLY: classify invented labels/)).toBeHidden();
  });

  test('never defaults unavailable operational values to zero', async ({ page }) => {
    await installRunDetailContractApi(page, 'cancelled');
    await page.goto(routeFor('cancelled'));

    const ledgerSection = page.getByRole('heading', { name: 'Provider and cost ledger' }).locator('xpath=..');
    await expect(ledgerSection.getByText('Not available')).toHaveCount(10);
    await expect(ledgerSection.locator('.workbench-provenance-unavailable')).toHaveCount(10);
    await expect(ledgerSection).not.toContainText('$0');
    await expect(ledgerSection).not.toContainText('0 USD');
  });

  for (const scenario of ['running', 'warning', 'failed', 'cancelled'] as const) {
    test(`renders the ${scenario} lifecycle without substituting inspection records`, async ({ page }) => {
      await installRunDetailContractApi(page, scenario);
      await page.goto(routeFor(scenario));
      const expected = scenario === 'warning' ? 'Completed with warnings' : `${scenario[0].toUpperCase()}${scenario.slice(1)}`;
      await expect(page.getByText(expected, { exact: true }).first()).toBeVisible();
      if (scenario === 'warning') {
        await expect(page.getByText(/BETA fixture record was unavailable/)).toBeVisible();
        await expect(page.getByText('Degradation detail')).toBeVisible();
      }
      if (scenario === 'failed') {
        await expect(page.getByText(/Fixture validation intentionally rejected/)).toBeVisible();
      }
      if (scenario === 'running' || scenario === 'failed' || scenario === 'cancelled') {
        await expect(page.getByRole('heading', { name: 'No artifacts have been recorded' })).toBeVisible();
      }
    });
  }

  test('keeps malformed detail and subresource failures route-local and retryable', async ({ page }) => {
    await installRunDetailContractApi(page, 'malformed');
    await page.goto(routeFor('malformed'));
    await expect(page.getByRole('heading', { name: 'The run response could not be interpreted' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'The artifacts response could not be interpreted' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Retry run overview' })).toBeVisible();
    await expect(page.getByText('API Error')).toHaveCount(0);
  });

  test('recovers the run overview from backend unavailability with route-local retry', async ({ page }) => {
    await installRunDetailContractApi(page, 'unavailable-once');
    await page.goto(routeFor('unavailable-once'));
    await expect(page.getByRole('heading', { name: 'This run is unavailable' })).toBeVisible();
    await expect(page.getByText('API Error')).toHaveCount(0);
    await page.getByRole('button', { name: 'Retry run overview' }).click();
    await expect(page.getByText('Completed', { exact: true }).first()).toBeVisible();
  });

  test('retains valid partial records while exposing malformed peers', async ({ page }) => {
    await installRunDetailContractApi(page, 'partial');
    await page.goto(routeFor('partial'));
    await expect(page.getByText('Some run fields could not be interpreted.')).toBeVisible();
    await expect(page.getByText('Some artifact fields could not be interpreted.')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'An inspectable dossier of invented archive labels' })).toBeVisible();
    await expect(page.getByText('Unknown state')).toBeVisible();
  });

  for (const viewport of [
    { name: 'desktop', width: 1280, height: 1000 },
    { name: '390px', width: 390, height: 844 },
  ]) {
    test(`${viewport.name} has keyboard citation parity, no A/AA axe violations, and no page overflow`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await installRunDetailContractApi(page, 'completed');
      await page.goto(routeFor('completed'));

      const citation = page.getByRole('button', { name: 'Inspect exact evidence 1' }).first();
      await citation.focus();
      await page.keyboard.press('Enter');
      await expect(page.getByRole('blockquote', { name: /Exact evidence excerpt/ })).toBeFocused();

      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze();
      expect(results.violations).toEqual([]);

      const widths = await page.evaluate(() => ({
        document: document.documentElement.scrollWidth,
        viewport: document.documentElement.clientWidth,
      }));
      expect(widths.document).toBeLessThanOrEqual(widths.viewport);
      await expect(page.getByText('Content hash').first()).toBeVisible();
      await expect(page.getByText('Fixture reference').first()).toBeVisible();
    });
  }

});
