import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';

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
                user_id: "default-user",
                query: {
                    main_query: payload.topic,
                    context: payload.description || ""
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
