const TOKEN_KEY = 'cerebro.access_token';

// Browser storage can be absent or throw (private windows, blocked site data).
export function getAccessToken(): string | null {
    try {
        return window.localStorage.getItem(TOKEN_KEY);
    } catch {
        return null;
    }
}

export function setAccessToken(token: string | null): void {
    try {
        if (token) window.localStorage.setItem(TOKEN_KEY, token);
        else window.localStorage.removeItem(TOKEN_KEY);
    } catch {
        // Token stays unset; requests go out unauthenticated and the server answers 401.
    }
}

/** The JWT `sub` claim: the server requires it as `user_id` when creating projects. */
export function tokenSubject(token: string | null = getAccessToken()): string | null {
    try {
        const payload = token?.split('.')[1];
        if (!payload) return null;
        const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
        const sub: unknown = JSON.parse(json).sub;
        return typeof sub === 'string' ? sub : null;
    } catch {
        return null;
    }
}
