import { AlertTriangle } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface IssuesBannerProps {
  heading: string;
  issues: readonly string[];
  className?: string;
}

export function IssuesBanner({ heading, issues, className }: IssuesBannerProps) {
  if (issues.length === 0) return null;

  return (
    <div
      className={cn(
        'flex items-start gap-3 rounded-md border border-current p-3 workbench-status-warning',
        className,
      )}
      role="status"
    >
      <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 flex-shrink-0" />
      <div className="text-sm leading-6">
        <p className="font-semibold">{heading}</p>
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted-foreground">
          {issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
