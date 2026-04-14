import { useQuery } from '@tanstack/react-query';
import { apiClient } from './client';

export const useMemoryNodes = () => {
    return useQuery({
        queryKey: ['memory', 'nodes'],
        queryFn: async () => {
            const { data } = await apiClient.get('/memory/nodes');
            return data;
        },
    });
};
