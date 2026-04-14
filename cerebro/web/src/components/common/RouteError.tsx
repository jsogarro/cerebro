import { useRouteError, isRouteErrorResponse } from 'react-router-dom';
import { AlertCircle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';

export function RouteError() {
    const error = useRouteError();

    let errorMessage = 'An unexpected error occurred.';
    if (isRouteErrorResponse(error)) {
        errorMessage = error.statusText || error.data;
    } else if (error instanceof Error) {
        errorMessage = error.message;
    }

    return (
        <div className="flex h-full w-full flex-col items-center justify-center p-6 text-center animate-in fade-in zoom-in duration-300">
            <div className="flex h-20 w-20 items-center justify-center rounded-full bg-destructive/10 text-destructive mb-6">
                <AlertCircle className="h-10 w-10" />
            </div>
            <h2 className="mb-2 text-2xl font-bold tracking-tight">Oops! Something went wrong</h2>
            <p className="mb-6 text-muted-foreground max-w-[500px]">
                {errorMessage}
            </p>
            <div className="flex space-x-4">
                <Button onClick={() => window.location.reload()}>
                    <RefreshCw className="mr-2 h-4 w-4" />
                    Reload Page
                </Button>
                <Button variant="outline" onClick={() => window.history.back()}>
                    Go Back
                </Button>
            </div>
        </div>
    );
}
