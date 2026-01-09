"use client";

import { useEffect } from "react";

import { Container } from "@/components/layout/container";
import { ErrorState } from "@/components/ui/error-state";

interface ErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function Error({ error, reset }: ErrorProps) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <Container className="py-12">
      <ErrorState
        title="Could not load this analysis"
        description={
          error.message ||
          "An unexpected error occurred while loading this analysis."
        }
        onRetry={reset}
        retryLabel="Retry"
      />
    </Container>
  );
}
