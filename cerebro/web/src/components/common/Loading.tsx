import { Skeleton } from '@/components/ui/skeleton';

export function PageLoading() {
    return (
        <div className="flex flex-col space-y-6 p-8 w-full animate-in fade-in duration-500">
            <div className="flex justify-between items-center">
                <div className="space-y-2">
                    <Skeleton className="h-8 w-[250px]" />
                    <Skeleton className="h-4 w-[350px]" />
                </div>
                <Skeleton className="h-10 w-[120px]" />
            </div>

            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-4 pt-4">
                {[1, 2, 3, 4].map((i) => (
                    <Skeleton key={i} className="h[120px] rounded-xl" />
                ))}
            </div>

            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3 pt-6">
                <div className="col-span-full lg:col-span-2 space-y-4">
                    <Skeleton className="h-8 w-[200px]" />
                    <Skeleton className="h-[300px] rounded-xl" />
                </div>
                <div className="space-y-4">
                    <Skeleton className="h-8 w-[150px]" />
                    <Skeleton className="h-[300px] rounded-xl" />
                </div>
            </div>
        </div>
    );
}
