const timestampFormatter = new Intl.DateTimeFormat('en-US', {
  dateStyle: 'medium',
  timeStyle: 'short',
});

export function formatTimestamp(value: string | null): string {
  if (value === null) return 'Not recorded';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return 'Not recorded';
  return timestampFormatter.format(parsed);
}

export function formatDurationMs(value: number, unit: string | null): string {
  if (unit !== null && unit !== 'ms') return `${value} ${unit}`;
  if (value < 1000) return `${Math.round(value)} ms`;
  const totalSeconds = value / 1000;
  if (totalSeconds < 60) return `${totalSeconds.toFixed(1)} s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.round(totalSeconds % 60);
  return `${minutes}m ${seconds}s`;
}

export function formatCost(value: number, unit: string | null): string {
  return unit ? `${value.toFixed(2)} ${unit}` : value.toFixed(2);
}

export function formatCount(value: number, unit: string | null): string {
  const rounded = Number.isInteger(value) ? value.toLocaleString() : value.toFixed(0);
  return unit ? `${rounded} ${unit}` : rounded;
}

export function describeQueryError(error: unknown): string {
  if (error instanceof Error && error.message.trim().length > 0) return error.message;
  return 'The request could not be completed.';
}
