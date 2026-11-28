import axios from 'axios';
import { toast } from '@/hooks/use-toast';
import { getAccessToken, setAccessToken } from './auth';

declare module 'axios' {
    interface AxiosRequestConfig {
        handleErrorLocally?: boolean;
    }
}

export const apiClient = axios.create({
    baseURL: '/api/v1',
    headers: {
        'Content-Type': 'application/json',
    },
});

// Request interceptor: attach the bearer token the server's auth middleware expects
apiClient.interceptors.request.use(
    (config) => {
        const token = getAccessToken();
        if (token) config.headers.set('Authorization', `Bearer ${token}`);
        return config;
    },
    (error) => Promise.reject(error)
);

interface ErrorEnvelope {
    error?: {
        code?: unknown;
        message?: unknown;
        details?: {
            errors?: Array<{ msg?: unknown }>;
        };
    };
    detail?: unknown;
}

/** Extracts a human-readable message from Cerebro's `{ error: { code, message, details } }` envelope. */
export function apiErrorMessage(error: unknown): string | null {
    if (!axios.isAxiosError(error)) return null;
    const data = error.response?.data as ErrorEnvelope | undefined;
    if (!data || typeof data !== 'object') return null;

    const firstValidationMsg = data.error?.details?.errors?.[0]?.msg;
    if (data.error?.code === 'VALIDATION_ERROR' && typeof firstValidationMsg === 'string' && firstValidationMsg.trim()) {
        return firstValidationMsg.replace(/^Value error,\s*/i, '');
    }
    if (typeof data.error?.message === 'string' && data.error.message.trim()) {
        return data.error.message;
    }
    if (typeof data.detail === 'string' && data.detail.trim()) {
        return data.detail;
    }
    return null;
}

// Response interceptor for error handling
apiClient.interceptors.response.use(
    (response) => response,
    (error) => {
        const url = axios.isAxiosError(error) ? (error.config?.url ?? '') : '';
        const isAuthTransition = url.endsWith('/auth/login') || url.endsWith('/auth/logout');

        // The access token expired or was revoked: sign in again rather than fail every call,
        // including routes that suppress global error toasts via handleErrorLocally.
        if (error.response?.status === 401 && !isAuthTransition && getAccessToken()) {
            setAccessToken(null);
            window.location.assign('/login');
            return Promise.reject(error);
        }

        if (axios.isAxiosError(error) && error.config?.handleErrorLocally) {
            return Promise.reject(error);
        }

        // Global error handling
        const message = apiErrorMessage(error) || error.message || 'An unexpected error occurred';
        toast({
            title: 'API Error',
            description: message,
            variant: 'destructive',
        });
        return Promise.reject(error);
    }
);
