import Link from "next/link";

import { Container } from "@/components/layout/container";

export function Footer() {
  return (
    <footer className="mt-16 border-t bg-background">
      <Container className="flex flex-col gap-2 py-6 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
        <p>
          AlphaAnalyst — open-source autonomous equity research agent.
        </p>
        <nav className="flex gap-4">
          <a
            href="https://github.com/"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-foreground"
          >
            GitHub
          </a>
          <Link href="/docs" className="hover:text-foreground">
            Docs
          </Link>
        </nav>
      </Container>
    </footer>
  );
}
