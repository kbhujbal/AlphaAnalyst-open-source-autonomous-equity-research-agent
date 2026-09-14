import Link from "next/link";
import { Container } from "@/components/layout/container";

export function Footer() {
  return (
    <footer className="mt-16 border-t bg-background print:hidden">
      <Container className="flex flex-col gap-2 py-6 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
        <p>AlphaAnalyst — equity research prototype.</p>
        <nav className="flex items-center gap-4">
          <a href="https://github.com/kbhujbal/AlphaAnalyst-open-source-autonomous-equity-research-agent" target="_blank" rel="noopener noreferrer" className="hover:text-foreground">GitHub</a>
          <Link href="/demo" className="hover:text-foreground">Offline demo</Link>
        </nav>
      </Container>
    </footer>
  );
}
