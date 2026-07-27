import type { Page, Route } from '@playwright/test';
import {
  runsContractFixtures,
  runsLedgerFixture,
} from '../fixtures/runs-contract-fixtures';

export type RunsLedgerScenario =
  | 'ready'
  | 'empty'
  | 'malformed'
  | 'partial'
  | 'unavailable-once'
  | 'loading';
export type RunsCancellationScenario =
  | 'success'
  | 'failure'
  | 'failure-long-detail'
  | 'delayed-success';

interface RunsContractApiOptions {
  ledger?: RunsLedgerScenario;
  cancellation?: RunsCancellationScenario;
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

export async function installRunsContractApi(
  page: Page,
  {
    ledger = 'ready',
    cancellation = 'success',
  }: RunsContractApiOptions = {},
) {
  let ledgerRequests = 0;
  let currentLedger = [...runsLedgerFixture];
  let releaseLedger = () => {};
  let releaseCancellation = () => {};
  const pendingLedger = new Promise<void>((resolve) => {
    releaseLedger = resolve;
  });
  const pendingCancellation = new Promise<void>((resolve) => {
    releaseCancellation = resolve;
  });

  await page.route('**/api/v1/runs**', async (route) => {
    const request = route.request();
    const relativePath = new URL(request.url()).pathname.replace('/api/v1', '');

    if (request.method() === 'GET' && relativePath === '/runs') {
      ledgerRequests += 1;
      if (ledger === 'loading' && ledgerRequests === 1) {
        await pendingLedger;
      }
      if (ledger === 'unavailable-once' && ledgerRequests <= 3) {
        await json(route, { detail: 'The contract fixture made the run ledger unavailable.' }, 503);
        return;
      }

      const items =
        ledger === 'empty'
          ? runsContractFixtures.empty
          : ledger === 'malformed'
            ? runsContractFixtures.malformed
            : ledger === 'partial'
              ? runsContractFixtures.partial
              : currentLedger;
      await json(route, { items });
      return;
    }

    const cancelMatch = relativePath.match(/^\/runs\/([^/]+)\/cancel$/);
    if (request.method() === 'POST' && cancelMatch) {
      const runId = decodeURIComponent(cancelMatch[1]);
      if (cancellation === 'failure' || cancellation === 'failure-long-detail') {
        const detail =
          cancellation === 'failure'
            ? 'The contract fixture rejected cancellation.'
            : `CancellationFailure_${'x'.repeat(320)}`;
        await json(route, { detail }, 409);
        return;
      }
      if (cancellation === 'delayed-success') {
        await pendingCancellation;
      }
      const cancelledRun = {
        ...runsContractFixtures.cancellation.success,
        run_id: runId,
      };
      currentLedger = currentLedger.map((run) =>
        run.run_id === runId ? cancelledRun : run,
      );
      await json(route, cancelledRun);
      return;
    }

    await json(route, { detail: `Unexpected Runs fixture request: ${relativePath}` }, 500);
  });

  return { releaseCancellation, releaseLedger };
}
