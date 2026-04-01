import { Info } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

interface EmptyChartProps {
  title: string;
  reason?: string;
}

/**
 * Reusable empty state for charts whose data isn't present in the current
 * MemoResponse. Charts surface this rather than silently rendering blank
 * axes, per CLAUDE.md's "show a typed empty state" rule.
 */
export function EmptyChart({ title, reason }: EmptyChartProps) {
  return (
    <Alert>
      <Info className="h-4 w-4" aria-hidden="true" />
      <AlertTitle>{title}</AlertTitle>
      {reason ? <AlertDescription>{reason}</AlertDescription> : null}
    </Alert>
  );
}
