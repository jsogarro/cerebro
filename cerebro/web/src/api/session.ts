import { apiClient } from './client';
import { getAccessToken, setAccessToken } from './auth';

interface LoginResponse {
    tokens: { access_token: string };
}

export async function signIn(email: string, password: string, totpCode?: string): Promise<void> {
    const trimmedCode = totpCode?.trim();
    const payload = trimmedCode
        ? { email, password, totp_code: trimmedCode }
        : { email, password };
    const { data } = await apiClient.post<LoginResponse>(
        '/auth/login',
        payload,
        { handleErrorLocally: true },
    );
    setAccessToken(data.tokens.access_token);
}

export async function signOut(): Promise<void> {
    try {
        if (getAccessToken()) {
            await apiClient.post('/auth/logout', undefined, { handleErrorLocally: true });
        }
    } catch {
        // Always clear local session state even if server-side revocation fails.
    } finally {
        setAccessToken(null);
    }
}
