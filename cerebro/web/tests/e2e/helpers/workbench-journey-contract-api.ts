import type { Page, Route } from '@playwright/test';
import { runDetailContractFixtures } from '../fixtures/run-detail-contract-fixtures';
import { runsLedgerFixture } from '../fixtures/runs-contract-fixtures';
import {
  canonicalWorkflowFixture,
  createdRunFixture,
} from '../fixtures/workflows-contract-fixtures';

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

export async function installWorkbenchJourneyContractApi(page: Page) {
  const runId = createdRunFixture.run_id;
  let detailRequests = 0;
  let terminalPublished = false;
  const resourceRequestCounts = new Map<string, number>();

  const withRunId = <T extends Record<string, unknown>>(record: T) => ({
    ...record,
    run_id: runId,
  });
  const terminalRun = {
    ...runDetailContractFixtures.runs.completed,
    run_id: runId,
    workflow_id: createdRunFixture.workflow_id,
    workflow_version: createdRunFixture.workflow_version,
    objective: createdRunFixture.objective,
  };
  const runningRun = {
    ...runDetailContractFixtures.runs.running,
    run_id: runId,
    workflow_id: createdRunFixture.workflow_id,
    workflow_version: createdRunFixture.workflow_version,
    objective: createdRunFixture.objective,
  };
  const terminalArtifact = {
    ...runDetailContractFixtures.artifact,
    run_id: runId,
    workflow_id: createdRunFixture.workflow_id,
    workflow_version: createdRunFixture.workflow_version,
  };

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/v1', '');

    if (request.method() === 'GET' && path === '/workflows') {
      await json(route, { items: [canonicalWorkflowFixture] });
      return;
    }
    if (request.method() === 'POST' && path === '/runs') {
      await json(route, createdRunFixture, 201);
      return;
    }
    if (request.method() === 'GET' && path === '/runs') {
      await json(route, {
        items: [
          terminalRun,
          ...runsLedgerFixture.filter((run) => run.run_id !== runId),
        ],
      });
      return;
    }
    if (request.method() === 'GET' && path === `/runs/${runId}`) {
      detailRequests += 1;
      if (detailRequests === 1) {
        await json(route, runningRun);
        return;
      }
      terminalPublished = true;
      await json(route, terminalRun);
      return;
    }

    const resourceMatch = path.match(
      new RegExp(`^/runs/${runId}/(tasks|events|evidence|artifacts|evaluations)$`),
    );
    if (request.method() === 'GET' && resourceMatch) {
      const resource = resourceMatch[1];
      const resourceRequests = (resourceRequestCounts.get(resource) ?? 0) + 1;
      resourceRequestCounts.set(resource, resourceRequests);
      if (!terminalPublished || resourceRequests === 1) {
        await json(route, { items: [] });
        return;
      }
      const items =
        resource === 'tasks'
          ? runDetailContractFixtures.tasks.map(withRunId)
          : resource === 'events'
            ? runDetailContractFixtures.events.map(withRunId)
            : resource === 'evidence'
              ? runDetailContractFixtures.evidence.map(withRunId)
              : resource === 'artifacts'
                ? [terminalArtifact]
                : [withRunId(runDetailContractFixtures.evaluation)];
      await json(route, { items });
      return;
    }

    await json(
      route,
      { detail: `Unexpected Workbench journey request: ${request.method()} ${path}` },
      500,
    );
  });

  return { runId, resourceRequestCounts };
}
