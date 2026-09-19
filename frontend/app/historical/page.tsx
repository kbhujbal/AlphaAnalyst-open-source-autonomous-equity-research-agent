import type { Metadata } from "next";
import { Container } from "@/components/layout/container";
import { HistoricalForm } from "@/components/historical/historical-form";

export const metadata: Metadata = { title: "Historical financial analysis — AlphaAnalyst" };

export default function HistoricalPage() {
  return <Container className="py-10 sm:py-14"><div className="mx-auto max-w-3xl space-y-8">
    <header className="space-y-3"><p className="text-sm font-medium text-muted-foreground">Historical research / SEC financials</p>
      <h1 className="text-3xl font-semibold tracking-tight">Financials available at your cutoff</h1>
      <p className="text-muted-foreground">Explore reported financial observations and source-linked calculations using historical 10-K and 10-Q filings, including amendments.</p>
    </header>
    <div className="rounded-lg border bg-muted/40 p-4 text-sm leading-relaxed">This analysis covers SEC financials. Historical prices, market caps, news, estimates and full research memos are unavailable. Current ticker-to-CIK mapping is used; historical issuer identity is unverified.</div>
    <div className="rounded-lg border bg-card p-5 sm:p-7"><HistoricalForm /></div>
  </div></Container>;
}
