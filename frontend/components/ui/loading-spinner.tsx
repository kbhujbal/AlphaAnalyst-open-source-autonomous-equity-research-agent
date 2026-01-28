import { Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

interface LoadingSpinnerProps {
  className?: string;
  label?: string;
}

export function LoadingSpinner({ className, label }: LoadingSpinnerProps) {
  return (
    <span
      role="status"
      aria-label={label ?? "Loading"}
      className={cn("inline-flex items-center gap-2 text-muted-foreground", className)}
    >
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      {label ? <span>{label}</span> : null}
    </span>
  );
}
