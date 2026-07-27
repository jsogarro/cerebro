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

// D1: the single consolidated workbench pill. Every semantic badge — claim
// support, evidence availability, run/task lifecycle, evaluation status — routes
// through here, colored by the semantics table's tone.
const toneClassByTone: Record<StateSemantics['tone'], string> = {
  neutral: 'wb-badge--neutral',
  informative: 'wb-badge--informative',
  positive: 'wb-badge--positive',
  caution: 'wb-badge--caution',
  critical: 'wb-badge--critical',
  muted: 'wb-badge--muted',
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
      className={cn('wb-badge', toneClassByTone[semantics.tone], className)}
      title={semantics.description}
    >
      <Icon aria-hidden="true" className="h-[0.85rem] w-[0.85rem] flex-shrink-0" />
      <span>
        {semantics.label}
        {rawValue ? <span className="wb-badge-raw"> (reported as "{rawValue}")</span> : null}
      </span>
    </span>
  );
}
