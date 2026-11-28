import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

export const SIGNED_IN_STATE = 'test-results/.auth/signed-in.json';

// Every spec except sign-in.spec.ts expects a signed-in browser, because the
// route guard sends anyone without a stored token to /login.
export default function globalSetup(config: { projects: Array<{ use: { baseURL?: string } }> }) {
    const origin = new URL(config.projects[0].use.baseURL as string).origin;
    mkdirSync(dirname(SIGNED_IN_STATE), { recursive: true });
    writeFileSync(
        SIGNED_IN_STATE,
        JSON.stringify({
            cookies: [],
            origins: [{ origin, localStorage: [{ name: 'cerebro.access_token', value: 'e2e.signed-in.token' }] }],
        }),
    );
}
