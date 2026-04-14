import { useQuery } from '@tanstack/react-query';
// import { apiClient } from './client';

export const useBenchmarks = () => {
    return useQuery({
        queryKey: ['benchmarks', 'list'],
        queryFn: async () => {
            // const { data } = await apiClient.get('/benchmarks/list');
            // return data;
            return [
                { id: "BM-1", name: "Math Reasoning", score: "89%", baseline: "80%" },
                { id: "BM-2", name: "Data Extraction", score: "96%", baseline: "92%" },
            ];
        },
    });
};
