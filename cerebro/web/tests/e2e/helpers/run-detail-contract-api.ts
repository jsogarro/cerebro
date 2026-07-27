import type { Page, Route } from '@playwright/test';
import { runDetailContractFixtures } from '../fixtures/run-detail-contract-fixtures';

export type RunDetailScenario =
  | 'completed'
  | 'running'
  | 'warning'
  | 'failed'
  | 'cancelled'
  | 'evidence-loading'
  | 'evidence-malformed'
  | 'evidence-only'
  | 'evidence-unavailable'
  | 'partial'
  | 'malformed'
  | 'unavailable-once';

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

export async function installRunDetailContractApi(
  page: Page,
  scenario: RunDetailScenario = 'completed',
) {
  const requestCounts = new Map<string, number>();
  let releaseEvidence = () => {};
  const pendingEvidence = new Promise<void>((resolve) => {
    releaseEvidence = resolve;
  });

  await page.route('**/api/v1/runs/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/v1', '');
    const count = (requestCounts.get(path) ?? 0) + 1;
    requestCounts.set(path, count);

    if (request.method() !== 'GET') {
      await json(route, { detail: `Unexpected detail fixture method for ${path}` }, 405);
      return;
    }
    if (scenario === 'unavailable-once' && count <= 3) {
      await json(route, { detail: `Contract fixture temporarily unavailable for ${path}` }, 503);
      return;
    }

    const match = path.match(/^\/runs\/([^/]+)(?:\/(tasks|events|evidence|artifacts|evaluations))?$/);
    if (!match) {
      await json(route, { detail: `Unexpected detail fixture request: ${path}` }, 500);
      return;
    }
    const resource = match[2];
    const effectiveScenario = scenario === 'unavailable-once' ? 'completed' : scenario;
    const runScenario =
      effectiveScenario === 'evidence-only' ||
      effectiveScenario === 'evidence-loading' ||
      effectiveScenario === 'evidence-unavailable' ||
      effectiveScenario === 'evidence-malformed'
        ? 'completed'
        : effectiveScenario;

    if (!resource) {
      await json(route, runDetailContractFixtures.runs[runScenario]);
      return;
    }

    if (effectiveScenario === 'malformed') {
      await json(route, { items: [{ unexpected: true }] });
      return;
    }
    const currentRun = runDetailContractFixtures.runs[runScenario];
    const currentRunId = typeof currentRun.run_id === 'string' ? currentRun.run_id : '';

    if (resource === 'tasks') {
      const items =
        effectiveScenario === 'failed'
          ? runDetailContractFixtures.failedTasks
          : effectiveScenario === 'cancelled'
            ? []
            : runDetailContractFixtures.tasks;
      await json(route, {
        items:
          effectiveScenario === 'partial'
            ? [...items.map((item) => ({ ...item, run_id: currentRunId })), { task_id: 91 }]
            : items.map((item) => ({ ...item, run_id: currentRunId })),
      });
      return;
    }
    if (resource === 'events') {
      await json(route, {
        items:
          effectiveScenario === 'partial'
            ? [
                ...runDetailContractFixtures.events.map((item) => ({ ...item, run_id: currentRunId })),
                { event_id: null },
              ]
            : runDetailContractFixtures.events.map((item) => ({ ...item, run_id: currentRunId })),
      });
      return;
    }
    if (resource === 'evidence') {
      if (effectiveScenario === 'evidence-loading') {
        await pendingEvidence;
      }
      if (effectiveScenario === 'evidence-unavailable') {
        await json(route, { detail: 'Evidence is unavailable in this contract scenario.' }, 503);
        return;
      }
      if (effectiveScenario === 'evidence-malformed') {
        await json(route, { items: [{ evidence_id: 42 }] });
        return;
      }
      const items =
        effectiveScenario === 'completed' ||
        effectiveScenario === 'warning' ||
        effectiveScenario === 'evidence-only' ||
        effectiveScenario === 'evidence-loading' ||
        effectiveScenario === 'partial'
          ? runDetailContractFixtures.evidence
          : [];
      await json(route, {
        items: items.map((item) => ({ ...item, run_id: currentRunId })),
      });
      return;
    }
    if (resource === 'artifacts') {
      const items =
        effectiveScenario === 'completed' ||
        effectiveScenario === 'warning' ||
        effectiveScenario === 'partial' ||
        effectiveScenario === 'evidence-loading' ||
        effectiveScenario === 'evidence-unavailable' ||
        effectiveScenario === 'evidence-malformed'
          ? [runDetailContractFixtures.artifact]
          : [];
      await json(route, {
        items:
          effectiveScenario === 'partial'
            ? [...items.map((item) => ({ ...item, run_id: currentRunId })), { artifact_id: 77 }]
            : items.map((item) => ({ ...item, run_id: currentRunId })),
      });
      return;
    }
    const items =
      effectiveScenario === 'completed' ||
      effectiveScenario === 'warning' ||
      effectiveScenario === 'partial'
        ? [runDetailContractFixtures.evaluation]
        : [];
    await json(route, {
      items: items.map((item) => ({ ...item, run_id: currentRunId })),
    });
  });

  return { releaseEvidence, requestCounts };
}
