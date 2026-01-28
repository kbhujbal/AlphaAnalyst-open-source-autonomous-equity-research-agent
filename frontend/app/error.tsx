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
    // Surface to the browser console; production logging belongs in a
    // future observability layer.
    console.error(error);
  }, [error]);

  return (
    <Container className="py-12">
      <ErrorState
        title="Something went wrong on this page"
        description={
          error.message
            ? error.message
            : "An unexpected error occurred while rendering this view."
        }
        onRetry={reset}
        retryLabel="Reload page"
      />
    </Container>
  );
}
