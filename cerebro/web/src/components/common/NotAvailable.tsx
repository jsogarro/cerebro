import { CircleDashed } from 'lucide-react';
import { WorkbenchStateNotice } from '@/components/runs/WorkbenchStateNotice';

/** Stands in for a page whose backend data source is not built; shows no figures. */
export function NotAvailable({ title, description, what }: { title: string; description: string; what: string }) {
    return (
        <div className="space-y-6 animate-in fade-in duration-500">
            <div>
                <h2 className="text-3xl font-bold tracking-tight">{title}</h2>
                <p className="text-muted-foreground">{description}</p>
            </div>
            <WorkbenchStateNotice
                tone="unavailable"
                icon={CircleDashed}
                kicker="Not available"
                heading={`${what} are not available yet`}
                message="The server does not publish this data yet, so nothing is shown here."
            />
        </div>
    );
}
