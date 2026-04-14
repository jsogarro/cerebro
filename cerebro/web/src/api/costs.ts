import { useQuery } from '@tanstack/react-query';
// import { apiClient } from './client';

export const useCostOverview = () => {
    return useQuery({
        queryKey: ['costs', 'overview'],
        queryFn: async () => {
            // const { data } = await apiClient.get('/costs/overview');
            // return data;
            return {
                totalCostToday: 124.50,
                currency: "USD",
                trend: "+14%"
            };
        },
    });
};
