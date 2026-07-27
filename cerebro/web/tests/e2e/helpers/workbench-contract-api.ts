import type { Page, Route } from '@playwright/test';
import {
  blockedWorkflowFixture,
  canonicalWorkflowFixture,
  createdRunFixture,
  incompatibleFixtureWorkflowFixture,
} from '../fixtures/workflows-contract-fixtures';

export type WorkflowCatalogScenario =
  | 'ready'
  | 'blocked'
  | 'incompatible-fixture'
  | 'unavailable-once';
export type CreateRunScenario =
  | 'success'
  | 'failure'
  | 'delayed-success'
  | 'malformed-success';

interface WorkbenchContractApiOptions {
  catalog?: WorkflowCatalogScenario;
  createRun?: CreateRunScenario;
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

export async function installWorkflowsContractApi(
  page: Page,
  {
    catalog = 'ready',
    createRun = 'success',
  }: WorkbenchContractApiOptions = {},
) {
  let catalogRequests = 0;
  let releaseCreateRun = () => {};
  const pendingCreateRun = new Promise<void>((resolve) => {
    releaseCreateRun = resolve;
  });

  await page.route('**/api/v1/workflows', async (route) => {
    catalogRequests += 1;
    if (catalog === 'unavailable-once' && catalogRequests === 1) {
      await json(route, { detail: 'The contract fixture made the catalog unavailable.' }, 503);
      return;
    }

    const workflows =
      catalog === 'blocked'
        ? [blockedWorkflowFixture]
        : catalog === 'incompatible-fixture'
          ? [canonicalWorkflowFixture, incompatibleFixtureWorkflowFixture]
          : [canonicalWorkflowFixture];
    await json(route, { items: workflows });
  });

  await page.route('**/api/v1/runs**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const relativePath = url.pathname.replace('/api/v1', '');

    if (request.method() === 'POST' && relativePath === '/runs') {
      if (createRun === 'failure') {
        await json(route, { detail: 'The contract fixture rejected this run submission.' }, 503);
        return;
      }
      if (createRun === 'delayed-success') {
        await pendingCreateRun;
      }
      if (createRun === 'malformed-success') {
        await json(route, { ...createdRunFixture, run_id: undefined }, 201);
        return;
      }
      await json(route, createdRunFixture, 201);
      return;
    }

    if (request.method() === 'GET' && relativePath === `/runs/${createdRunFixture.run_id}`) {
      await json(route, createdRunFixture);
      return;
    }

    if (
      request.method() === 'GET' &&
      relativePath.startsWith(`/runs/${createdRunFixture.run_id}/`)
    ) {
      await json(route, { items: [] });
      return;
    }

    await json(route, { detail: `Unexpected workbench fixture request: ${relativePath}` }, 500);
  });

  return { releaseCreateRun };
}
