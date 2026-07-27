import { CircleDashed } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { OriginValue, ValueOrigin } from '@/api/workbench';

const provenanceLabel: Record<ValueOrigin, string> = {
  measured: 'Measured',
  'fixture-derived': 'Fixture-derived',
  unavailable: 'Unavailable',
};

const provenanceClass: Record<ValueOrigin, string> = {
  measured: 'workbench-provenance workbench-provenance-measured',
  'fixture-derived': 'workbench-provenance workbench-provenance-fixture',
  unavailable: 'workbench-provenance workbench-provenance-unavailable',
};

function defaultFormat(value: string | number, unit: string | null): string {
  if (typeof value === 'number') {
    const rendered = Number.isInteger(value) ? value.toLocaleString() : value.toFixed(2);
    return unit ? `${rendered} ${unit}` : rendered;
  }
  return unit ? `${value} ${unit}` : value;
}

export interface OriginValueDisplayProps<T extends string | number> {
  value: OriginValue<T>;
  label?: string;
  format?: (value: T, unit: string | null) => string;
  className?: string;
}

export function OriginValueDisplay<T extends string | number>({
  value,
  label,
  format,
  className,
}: OriginValueDisplayProps<T>) {
  const formatted =
    value.value === null
      ? 'Not available'
      : (format ?? defaultFormat)(value.value, value.unit);

  return (
    <span className={cn('inline-flex flex-wrap items-center gap-2', className)}>
      {label ? (
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
      ) : null}
      <span className={value.origin === 'unavailable' ? 'text-muted-foreground' : 'font-medium'}>
        {formatted}
      </span>
      <span className={provenanceClass[value.origin]}>
        {value.origin === 'unavailable' ? (
          <CircleDashed aria-hidden="true" className="h-3.5 w-3.5" />
        ) : null}
        {provenanceLabel[value.origin]}
      </span>
    </span>
  );
}
