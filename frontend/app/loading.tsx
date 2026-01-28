import { Container } from "@/components/layout/container";
import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <Container className="py-12">
      <div className="mx-auto max-w-3xl space-y-4 text-center">
        <Skeleton className="mx-auto h-12 w-2/3" />
        <Skeleton className="mx-auto h-6 w-3/4" />
        <Skeleton className="mx-auto h-12 w-full max-w-md" />
      </div>
      <div className="mt-16 grid gap-4 md:grid-cols-3">
        <Skeleton className="h-44 w-full" />
        <Skeleton className="h-44 w-full" />
        <Skeleton className="h-44 w-full" />
      </div>
    </Container>
  );
}
