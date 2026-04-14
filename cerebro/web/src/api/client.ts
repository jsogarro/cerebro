import axios from 'axios';

export const apiClient = axios.create({
    baseURL: '/api/v1',
    headers: {
        'Content-Type': 'application/json',
    },
});

// Request interceptor for auth (future use)
apiClient.interceptors.request.use(
    (config) => {
        // Add auth token when implemented
        return config;
    },
    (error) => Promise.reject(error)
);

import { toast } from '@/hooks/use-toast';

// Response interceptor for error handling
apiClient.interceptors.response.use(
    (response) => response,
    (error) => {
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
