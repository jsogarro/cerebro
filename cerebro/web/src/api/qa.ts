import { useQuery } from '@tanstack/react-query';
// import { apiClient } from './client';

export const useQaMetrics = () => {
    return useQuery({
        queryKey: ['qa', 'metrics'],
        queryFn: async () => {
            // const { data } = await apiClient.get('/qa/metrics');
            // return data;
            return [
                { id: "QA-1", title: "Fact Check Pass Rate", value: "98%", status: "healthy" },
                { id: "QA-2", title: "Citation Accuracy", value: "92%", status: "warning" },
            ];
        },
    });
};
