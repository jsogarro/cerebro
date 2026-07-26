import type { LucideIcon } from 'lucide-react';
import {
  Activity,
  AlertTriangle,
  Ban,
  Check,
  CheckCircle2,
  CircleDashed,
  Clock,
  Database,
  FileWarning,
  HelpCircle,
  Link,
  Link2,
  Unlink,
  X,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { SemanticIconKey, StateSemantics } from '@/api/workbench';

const iconByKey: Record<SemanticIconKey, LucideIcon> = {
  clock: Clock,
  activity: Activity,
  'check-warning': CheckCircle2,
  check: Check,
  x: X,
  ban: Ban,
  help: HelpCircle,
  link: Link,
  'link-partial': Link2,
  unlink: Unlink,
  alert: AlertTriangle,
  database: Database,
  'database-off': CircleDashed,
  'file-warning': FileWarning,
};

const toneClassByTone: Record<StateSemantics['tone'], string> = {
  neutral: 'workbench-status-neutral',
  informative: 'workbench-status-informative',
  positive: 'workbench-status-success',
  caution: 'workbench-status-warning',
  critical: 'workbench-status-failed',
  muted: 'workbench-status-muted',
};

export interface StatusBadgeProps {
  semantics: StateSemantics;
  rawValue?: string | null;
  className?: string;
}

export function StatusBadge({ semantics, rawValue, className }: StatusBadgeProps) {
  const Icon = iconByKey[semantics.iconKey];
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 text-sm font-medium',
        toneClassByTone[semantics.tone],
        className,
      )}
      title={semantics.description}
    >
      <Icon aria-hidden="true" className="h-4 w-4 flex-shrink-0" />
      <span>
        {semantics.label}
        {rawValue ? ` (reported as "${rawValue}")` : ''}
      </span>
    </span>
  );
}
