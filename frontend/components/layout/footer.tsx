import Link from "next/link";

import { Container } from "@/components/layout/container";

export function Footer() {
  return (
    <footer className="mt-16 border-t bg-background print:hidden">
      <Container className="flex flex-col gap-2 py-6 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
        <p>AlphaAnalyst — open-source autonomous equity research agent.</p>
        <nav className="flex items-center gap-4">
          <a
            href="https://github.com/"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-foreground"
          >
            GitHub
          </a>
          <a
            href="https://github.com/"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="GitHub stars"
          >
            {/* eslint-disable-next-line @next/next/no-img-element -- shields.io is a third-party shield SVG, no Next/Image optimization needed */}
            <img
              src="https://img.shields.io/badge/GitHub-stars-181717?logo=github&logoColor=white"
              alt="GitHub stars"
              className="h-5"
              loading="lazy"
            />
          </a>
          <Link href="/docs" className="hover:text-foreground">
            Docs
          </Link>
        </nav>
      </Container>
    </footer>
  );
}
