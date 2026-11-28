import { useQuery } from '@tanstack/react-query';
import { apiClient } from './client';

interface AgentInfo {
    agent_type: string;
    name: string;
    description: string;
}

export const useAgents = () => {
    return useQuery({
        queryKey: ['agents'],
        queryFn: async () => {
            const { data } = await apiClient.get<{ agents: AgentInfo[] }>('/agents');
            // The server lists capabilities, not live state, so `status` is only "available".
            return data.agents.map((agent) => ({
                id: agent.agent_type,
                name: agent.name,
                role: agent.description,
                status: 'available',
            }));
        },
    });
};
