import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';
import { tokenSubject } from './auth';

export interface CreateResearchPayload {
    title: string;
    topic: string;
    description?: string;
}

export const useResearchProjects = () => {
    return useQuery({
        queryKey: ['research', 'projects'],
        queryFn: async () => {
            const { data } = await apiClient.get('/research/projects');
            return data;
        },
    });
};

export const useResearchProject = (id: string) => {
    return useQuery({
        queryKey: ['research', 'project', id],
        queryFn: async () => {
            const { data } = await apiClient.get(`/research/projects/${id}`);
            return data;
        },
        enabled: !!id,
    });
};

export const useCreateResearch = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async (payload: CreateResearchPayload) => {
            const backendPayload = {
                title: payload.title,
                user_id: tokenSubject(),
                // The form has no domain picker yet; "general" is the server's catch-all domain.
                query: {
                    text: payload.topic,
                    domains: ['general'],
                }
            };
            const { data } = await apiClient.post('/research/projects', backendPayload);
            return data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['research', 'projects'] });
        },
    });
};
