"use client";

import { AlertTriangle } from "lucide-react";
import type { ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

interface ErrorStateProps {
  title?: string;
  description?: ReactNode;
  onRetry?: () => void;
  retryLabel?: string;
}

export function ErrorState({
  title = "Something went wrong",
  description,
  onRetry,
  retryLabel = "Try again",
}: ErrorStateProps) {
  return (
    <Alert variant="destructive" className="space-y-3">
      <AlertTriangle className="h-4 w-4" aria-hidden="true" />
      <AlertTitle>{title}</AlertTitle>
      {description ? <AlertDescription>{description}</AlertDescription> : null}
      {onRetry ? (
        <div>
          <Button variant="outline" size="sm" onClick={onRetry}>
            {retryLabel}
          </Button>
        </div>
      ) : null}
    </Alert>
  );
}
