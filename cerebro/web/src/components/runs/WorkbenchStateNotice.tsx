import type { ComponentType } from 'react';
import { CircleDashed, FileWarning, Inbox, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

export type WorkbenchNoticeTone = 'loading' | 'empty' | 'unavailable' | 'malformed';

interface IconProps {
  className?: string;
  'aria-hidden'?: boolean | 'true' | 'false';
}

const toneDefaults: Record<
  WorkbenchNoticeTone,
  { icon: ComponentType<IconProps>; badgeLabel: string; badgeClass: string; iconSpin: boolean }
> = {
  loading: {
    icon: Loader2,
    badgeLabel: 'Loading',
    badgeClass: 'workbench-provenance workbench-status-informative',
    iconSpin: true,
  },
  empty: {
    icon: Inbox,
    badgeLabel: 'No records',
    badgeClass: 'workbench-provenance workbench-status-neutral',
    iconSpin: false,
  },
  unavailable: {
    icon: CircleDashed,
    badgeLabel: 'Data unavailable',
    badgeClass: 'workbench-provenance workbench-provenance-unavailable',
    iconSpin: false,
  },
  malformed: {
    icon: FileWarning,
    badgeLabel: 'Malformed response',
    badgeClass: 'workbench-provenance workbench-status-failed',
    iconSpin: false,
  },
};

export interface WorkbenchStateNoticeProps {
  tone: WorkbenchNoticeTone;
  kicker: string;
  heading: string;
  message?: string | null;
  issues?: readonly string[];
  icon?: ComponentType<IconProps>;
  className?: string;
}

export function WorkbenchStateNotice({
  tone,
  kicker,
  heading,
  message,
  issues,
  icon,
  className,
}: WorkbenchStateNoticeProps) {
  const defaults = toneDefaults[tone];
  const Icon = icon ?? defaults.icon;

  return (
    <div className={cn('workbench-empty-state', className)} role="status">
      <span className="workbench-empty-mark" aria-hidden="true">
        <Icon aria-hidden="true" className={cn('h-5 w-5', defaults.iconSpin && 'animate-spin')} />
      </span>
      <div>
        <p className="workbench-kicker">{kicker}</p>
        <h3 className="font-editorial mt-1 text-xl">{heading}</h3>
        {message ? (
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">{message}</p>
        ) : null}
        {issues && issues.length > 0 ? (
          <ul className="mt-2 max-w-2xl list-disc space-y-1 pl-5 text-sm leading-6 text-muted-foreground">
            {issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        ) : null}
      </div>
      <span className={defaults.badgeClass}>{defaults.badgeLabel}</span>
    </div>
  );
}
