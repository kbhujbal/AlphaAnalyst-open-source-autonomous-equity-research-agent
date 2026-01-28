import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface ContainerProps {
  children: ReactNode;
  className?: string;
}

export function Container({ children, className }: ContainerProps) {
  return (
    <div className={cn("mx-auto w-full max-w-6xl px-4", className)}>
      {children}
    </div>
  );
}
