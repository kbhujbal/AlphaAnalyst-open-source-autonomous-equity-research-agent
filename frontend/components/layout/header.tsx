import { Github } from "lucide-react";
import Link from "next/link";

import { Container } from "@/components/layout/container";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import { Button } from "@/components/ui/button";

export function Header() {
  return (
    <header className="border-b bg-background print:hidden">
      <Container className="flex h-14 items-center justify-between">
        <Link href="/" className="flex items-center gap-2">
          <span className="text-lg font-semibold tracking-tight">
            AlphaAnalyst
          </span>
        </Link>
        <nav className="flex items-center gap-1">
          <Button variant="ghost" size="sm" asChild>
            <Link href="/">Home</Link>
          </Button>
          <Button variant="ghost" size="sm" asChild>
            <Link href="/about">About</Link>
          </Button>
          <Button variant="ghost" size="icon" asChild>
            <a
              href="https://github.com/"
              target="_blank"
              rel="noopener noreferrer"
              aria-label="GitHub repository"
            >
              <Github className="h-4 w-4" aria-hidden="true" />
            </a>
          </Button>
          <ThemeToggle />
        </nav>
      </Container>
    </header>
  );
}
