import { expect, test } from '@playwright/test';
import process from 'node:process';
import { observeRuntime } from './helpers/runtime-observer';

const realBackendEnabled = process.env.PLAYWRIGHT_REAL_BACKEND === '1';
const backendUrl =
  process.env.PLAYWRIGHT_BACKEND_URL ??
  process.env.VITE_API_URL ??
  'http://127.0.0.1:8000';
const requiredOperations = {
  '/api/v1/workflows': ['get'],
  '/api/v1/workflows/{workflow_id}': ['get'],
  '/api/v1/runs': ['get', 'post'],
  '/api/v1/runs/{run_id}': ['get'],
  '/api/v1/runs/{run_id}/cancel': ['post'],
  '/api/v1/runs/{run_id}/tasks': ['get'],
  '/api/v1/runs/{run_id}/events': ['get'],
  '/api/v1/runs/{run_id}/evidence': ['get'],
  '/api/v1/runs/{run_id}/artifacts': ['get'],
  '/api/v1/runs/{run_id}/evaluations': ['get'],
} as const;
const requiredSchemaFamilies = [
  'workflow',
  'run',
  'task',
  'runevent',
  'evidence',
  'claimsupport',
  'artifact',
  'evaluation',
] as const;

test.describe('real-backend Golden Run', () => {
  test.skip(
    !realBackendEnabled,
    'Set PLAYWRIGHT_REAL_BACKEND=1 only after the backend startup and OpenAPI handoff is ready.',
  );

  test('starts and inspects the fixture-backed Golden Run without intercepted workbench APIs', async ({
    page,
    playwright,
  }) => {
    test.setTimeout(150_000);
    const runtime = observeRuntime(page);
    const backend = await playwright.request.newContext({ baseURL: backendUrl });
    let openApi: {
      paths?: Record<string, Record<string, unknown>>;
      components?: { schemas?: Record<string, unknown> };
    };
    let openApiStatus: number;
    try {
      const response = await backend.get('/openapi.json', { timeout: 10_000 });
      openApiStatus = response.status();
      openApi = await response.json();
    } catch (error) {
      throw new Error(
        `PLAYWRIGHT_REAL_BACKEND=1 requires a reachable backend OpenAPI document at ${backendUrl}/openapi.json. Start the handed-off backend or set PLAYWRIGHT_BACKEND_URL. Cause: ${String(error)}`,
      );
    } finally {
      await backend.dispose();
    }

    expect(
      openApiStatus!,
      `Expected backend OpenAPI readiness at ${backendUrl}/openapi.json.`,
    ).toBe(200);
    const missingOperations = Object.entries(requiredOperations).flatMap(
      ([path, methods]) =>
        methods
          .filter((method) => !(method in (openApi.paths?.[path] ?? {})))
          .map((method) => `${method.toUpperCase()} ${path}`),
    );
    expect(
      missingOperations,
      `Backend OpenAPI handoff is incomplete. Missing required workbench operations: ${missingOperations.join(', ')}`,
    ).toEqual([]);
    const schemaNames = Object.keys(openApi.components?.schemas ?? {}).map((name) =>
      name.toLowerCase().replaceAll('_', ''),
    );
    const missingSchemaFamilies = requiredSchemaFamilies.filter(
      (family) => !schemaNames.some((name) => name.includes(family)),
    );
    expect(
      missingSchemaFamilies,
      `Backend OpenAPI handoff is incomplete. Missing workbench schema families: ${missingSchemaFamilies.join(', ')}`,
    ).toEqual([]);

    await page.goto('/app/workflows');
    await expect(page.getByRole('heading', { name: 'Workflows' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start controlled run' })).toBeEnabled({
      timeout: 15_000,
    });
    await page.getByRole('button', { name: 'Start controlled run' }).click();
    await expect(page).toHaveURL(/\/app\/runs\/[^/]+$/, { timeout: 15_000 });

    await expect(
      page.getByRole('heading', { name: 'Research artifact', exact: true }),
    ).toBeVisible({ timeout: 120_000 });
    const citation = page.getByRole('button', { name: /Inspect exact evidence/ }).first();
    await expect(citation).toBeVisible();
    await citation.click();
    await expect(page.getByRole('blockquote', { name: /Exact evidence excerpt/ })).toBeFocused();
    await expect(page.getByRole('heading', { name: 'Evaluation ledger' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Provider and cost ledger' })).toBeVisible();

    runtime.stop();
    expect(runtime.evidence, 'real-backend browser console and network evidence').toEqual({
      consoleErrors: [],
      pageErrors: [],
      requestFailures: [],
      unexpectedHttpFailures: [],
      expectedHttpFailures: [],
    });
  });
});
