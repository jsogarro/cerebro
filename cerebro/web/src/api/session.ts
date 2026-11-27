import { apiClient } from './client';
import { setAccessToken } from './auth';

interface LoginResponse {
    tokens: { access_token: string };
}

export async function signIn(email: string, password: string): Promise<void> {
    const { data } = await apiClient.post<LoginResponse>(
        '/auth/login',
        { email, password },
        { handleErrorLocally: true },
    );
    setAccessToken(data.tokens.access_token);
}
