import type {
  ConsoleMessage,
  Page,
  Request,
  Response,
} from '@playwright/test';

export interface ExpectedHttpFailure {
  method: string;
  path: string;
  status: number;
}

export interface RuntimeEvidence {
  consoleErrors: string[];
  pageErrors: string[];
  requestFailures: string[];
  unexpectedHttpFailures: string[];
  expectedHttpFailures: string[];
}

export function observeRuntime(
  page: Page,
  expectedHttpFailures: readonly ExpectedHttpFailure[] = [],
) {
  const evidence: RuntimeEvidence = {
    consoleErrors: [],
    pageErrors: [],
    requestFailures: [],
    unexpectedHttpFailures: [],
    expectedHttpFailures: [],
  };

  const onConsole = (message: ConsoleMessage) => {
    if (message.type() === 'error') evidence.consoleErrors.push(message.text());
  };
  const onPageError = (error: Error) => {
    evidence.pageErrors.push(error.message);
  };
  const onRequestFailed = (request: Request) => {
    evidence.requestFailures.push(
      `${request.method()} ${request.url()} — ${request.failure()?.errorText ?? 'unknown failure'}`,
    );
  };
  const onResponse = (response: Response) => {
    if (response.status() < 400) return;
    const request = response.request();
    const path = new URL(response.url()).pathname;
    const summary = `${request.method()} ${path} — ${response.status()}`;
    const expected = expectedHttpFailures.some(
      (failure) =>
        failure.method === request.method() &&
        failure.path === path &&
        failure.status === response.status(),
    );
    (expected ? evidence.expectedHttpFailures : evidence.unexpectedHttpFailures).push(summary);
  };

  page.on('console', onConsole);
  page.on('pageerror', onPageError);
  page.on('requestfailed', onRequestFailed);
  page.on('response', onResponse);

  return {
    evidence,
    stop() {
      page.off('console', onConsole);
      page.off('pageerror', onPageError);
      page.off('requestfailed', onRequestFailed);
      page.off('response', onResponse);
    },
  };
}
