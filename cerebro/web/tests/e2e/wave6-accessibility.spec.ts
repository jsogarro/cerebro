import AxeBuilder from '@axe-core/playwright';
import { expect, type Page, type Route, test } from '@playwright/test';
import {
  completedDetailRun,
  detailArtifact,
  detailEvidence,
  runningDetailRun,
} from './fixtures/run-detail-contract-fixtures';
import { installRunDetailContractApi } from './helpers/run-detail-contract-api';
import { installRunsContractApi } from './helpers/runs-contract-api';
import { installWorkflowsContractApi } from './helpers/workbench-contract-api';

const longToken = 'long-unbroken-provenance-token-'.repeat(24);
const longObjective =
  'Inspect an intentionally long controlled-research objective that preserves every qualifier, limitation, and provenance requirement while the interface reflows without hiding the evidence needed to assess the resulting claims. '.repeat(
    4,
  );
const longFailure =
  `The controlled backend returned a deliberately long failure explanation: ${'failure-detail-'.repeat(48)}`;

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

async function installLongContentDetail(page: Page) {
  await installRunDetailContractApi(page, 'completed');
  await page.route('**/api/v1/runs/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/v1', '');
    if (request.method() !== 'GET') {
      await route.fallback();
      return;
    }

    if (path === '/runs/run-detail-completed') {
      await json(route, {
        ...completedDetailRun,
        objective: longObjective,
        warnings: [longFailure],
        failure_summary: longFailure,
      });
      return;
    }
    if (path === '/runs/run-detail-completed/evidence') {
      await json(route, {
        items: detailEvidence.map((item, index) => ({
          ...item,
          run_id: 'run-detail-completed',
          title:
            index === 0
              ? `Invented archive source with a deliberately long descriptive title ${longObjective}`
              : item.title,
          content_hash: index === 0 ? `sha256:${longToken}` : item.content_hash,
          excerpt: index === 1 ? '   ' : item.excerpt,
        })),
      });
      return;
    }
    if (path === '/runs/run-detail-completed/artifacts') {
      await json(route, {
        items: [
          {
            ...detailArtifact,
            run_id: 'run-detail-completed',
            content_hash: `sha256:${longToken}`,
            content: {
              ...detailArtifact.content,
              title: `A long-form inspectable artifact ${longObjective}`,
            },
          },
        ],
      });
      return;
    }
    await route.fallback();
  });
}

async function expectNoAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(results.violations).toEqual([]);
}

async function expectNoPageOverflow(page: Page) {
  const widths = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth,
    viewport: document.documentElement.clientWidth,
  }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport);
}

async function expectHeadingHierarchy(page: Page) {
  await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
  await expect(page.getByRole('main')).toHaveCount(1);
  const levels = await page.locator('h1, h2, h3, h4, h5, h6').evaluateAll((headings) =>
    headings
      .filter((heading) => {
        const style = window.getComputedStyle(heading);
        return style.display !== 'none' && style.visibility !== 'hidden';
      })
      .map((heading) => Number(heading.tagName.slice(1))),
  );
  expect(levels[0]).toBe(1);
  for (let index = 1; index < levels.length; index += 1) {
    expect(levels[index]).toBeLessThanOrEqual(levels[index - 1] + 1);
  }
}

test.describe('Wave 6 responsive and accessibility hardening', () => {
  test('keeps one main/h1 and a non-skipping heading hierarchy across the workbench journey', async ({
    page,
  }) => {
    await installWorkflowsContractApi(page);
    await page.goto('/app/workflows');
    await expectHeadingHierarchy(page);

    await installRunsContractApi(page);
    await page.goto('/app/runs');
    await expectHeadingHierarchy(page);

    await installRunDetailContractApi(page, 'completed');
    await page.goto('/app/runs/run-detail-completed');
    await expectHeadingHierarchy(page);
  });

  test('supports skip navigation and associates each budget error with its field', async ({
    page,
  }) => {
    await installWorkflowsContractApi(page);
    await page.goto('/app/workflows');

    await page.keyboard.press('Tab');
    const skipLink = page.getByRole('link', { name: 'Skip to main content' });
    await expect(skipLink).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('main')).toBeFocused();

    const timeBudget = page.getByLabel('Time limit');
    const costBudget = page.getByLabel('Cost limit');
    await timeBudget.fill('0');
    await costBudget.fill('-1');
    await expect(timeBudget).toHaveAttribute('aria-invalid', 'true');
    await expect(timeBudget).toHaveAttribute(
      'aria-describedby',
      'time-budget-unit time-budget-error',
    );
    await expect(costBudget).toHaveAttribute('aria-invalid', 'true');
    await expect(costBudget).toHaveAttribute(
      'aria-describedby',
      'cost-budget-unit cost-budget-error',
    );
    await expect(page.getByText('Enter a positive whole number of seconds.')).toBeVisible();
    await expect(page.getByText('Enter a non-negative cost limit.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Resolve blocked checks' })).toBeDisabled();

    await timeBudget.fill('600');
    await costBudget.fill('0');
    await expect(timeBudget).toHaveAttribute('aria-invalid', 'false');
    await expect(costBudget).toHaveAttribute('aria-invalid', 'false');
    await expect(page.getByRole('button', { name: 'Start controlled run' })).toBeEnabled();
  });

  test('uses isolated live regions and announces only a real run lifecycle change', async ({
    page,
  }) => {
    await installWorkflowsContractApi(page, { catalog: 'unavailable-once' });
    await page.goto('/app/workflows');
    await expect(page.getByRole('alert')).toContainText('Workflow service is unavailable');
    await expect(page.locator('[role="alert"] [role="status"], [role="status"] [role="alert"]')).toHaveCount(0);

    const runsApi = await installRunsContractApi(page, { cancellation: 'delayed-success' });
    await page.goto('/app/runs');
    const pendingRow = page.getByRole('row').filter({
      hasText: 'Await a controlled source pack before beginning fixture research.',
    });
    await pendingRow.getByRole('button', { name: 'Cancel run' }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Run status updated' })).toHaveCount(0);
    runsApi.releaseCancellation();
    await expect(page.getByRole('status').filter({ hasText: 'Run status updated' })).toContainText(
      'is now Cancelled',
    );
  });

  test('announces a polled detail lifecycle change without announcing initial state', async ({
    page,
  }) => {
    await installRunDetailContractApi(page, 'completed');
    let overviewRequests = 0;
    await page.route('**/api/v1/runs/run-detail-lifecycle-update', async (route) => {
      overviewRequests += 1;
      const fixture = overviewRequests === 1 ? runningDetailRun : completedDetailRun;
      await json(route, { ...fixture, run_id: 'run-detail-lifecycle-update' });
    });
    await page.goto('/app/runs/run-detail-lifecycle-update');

    await expect(page.getByText('Running', { exact: true }).first()).toBeVisible();
    const lifecycleStatus = page
      .getByRole('status')
      .filter({ hasText: 'Run status updated to Completed.' });
    await expect(lifecycleStatus).toHaveCount(0);
    await expect(lifecycleStatus).toContainText('Run status updated to Completed.', {
      timeout: 5_000,
    });
  });

  for (const surface of [
    { name: 'desktop-light', width: 1280, height: 900, dark: false },
    { name: '390px-light', width: 390, height: 844, dark: false },
    { name: '200-percent-equivalent', width: 640, height: 720, dark: false },
    { name: 'desktop-dark', width: 1280, height: 900, dark: true },
  ]) {
    test(`${surface.name} preserves long-content provenance with axe-clean reflow`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: surface.width, height: surface.height });
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await installLongContentDetail(page);
      await page.goto('/app/runs/run-detail-completed');
      if (surface.dark) {
        await page.getByRole('button', { name: /Toggle color theme; switch to dark theme/ }).click();
        await expect(page.locator('html')).toHaveClass(/dark/);
      }

      await expect(page.getByRole('heading', { name: longObjective, exact: true })).toBeVisible();
      await expect(page.getByText(`sha256:${longToken}`).first()).toBeVisible();
      await expect(page.getByText('No exact excerpt is available.').first()).toBeVisible();
      await expectNoPageOverflow(page);
      await expectNoAxeViolations(page);

      const citation = page.getByRole('button', { name: 'Inspect exact evidence 1' }).first();
      await citation.focus();
      await page.keyboard.press('Enter');
      await expect(page.getByRole('blockquote', { name: /Exact evidence excerpt/ })).toBeFocused();
      await page.getByRole('button', { name: 'Return to claim' }).click();
      await expect(citation).toBeFocused();

      const motion = await page.evaluate(() => {
        const pageSurface = document.querySelector('.workbench-page');
        const evidence = document.querySelector('.run-evidence-record');
        return {
          pageAnimation: pageSurface ? getComputedStyle(pageSurface).animationDuration : '',
          evidenceTransition: evidence ? getComputedStyle(evidence).transitionDuration : '',
        };
      });
      expect(motion.pageAnimation).toBe('0.001s');
      expect(motion.evidenceTransition.split(',').every((value) => value.trim() === '0.001s')).toBe(
        true,
      );

    });
  }
});
