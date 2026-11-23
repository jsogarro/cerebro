import axios from 'axios';
import { toast } from '@/hooks/use-toast';
import { getAccessToken } from './auth';

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

// Response interceptor for error handling
apiClient.interceptors.response.use(
    (response) => response,
    (error) => {
        if (axios.isAxiosError(error) && error.config?.handleErrorLocally) {
            return Promise.reject(error);
        }

        // Global error handling
        const message = error.response?.data?.detail || error.message || 'An unexpected error occurred';
        toast({
            title: 'API Error',
            description: message,
            variant: 'destructive',
        });
        return Promise.reject(error);
    }
);
