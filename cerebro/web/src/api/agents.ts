import { useQuery } from '@tanstack/react-query';
import { apiClient } from './client';

export const useAgents = () => {
    return useQuery({
        queryKey: ['agents'],
        queryFn: async () => {
            const { data } = await apiClient.get('/agents');
            return data;
        },
    });
};

export const useAgentLogs = (agentId: string) => {
    return useQuery({
        queryKey: ['agents', 'logs', agentId],
        queryFn: async () => {
            const { data } = await apiClient.get(`/agents/${agentId}/logs`);
            return data;
        },
        enabled: !!agentId,
    });
};
