import { FileText, Newspaper, Calculator } from "lucide-react";

import { Container } from "@/components/layout/container";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function HomePage() {
  return (
    <Container className="py-12">
      <section className="mx-auto max-w-3xl text-center">
        <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
          AlphaAnalyst
        </h1>
        <p className="mt-4 text-lg text-muted-foreground">
          Open-source autonomous equity research agent. Given a US stock ticker,
          produce a research memo with DCF valuation, news analysis, and full
          citations.
        </p>
        <div className="mt-8">
          {/* TODO Phase 15: replace with TickerSearch client island */}
          <div
            id="ticker-search-placeholder"
            className="mx-auto flex h-12 max-w-md items-center justify-center rounded-md border border-dashed text-sm text-muted-foreground"
          >
            Ticker search (Phase 15)
          </div>
        </div>
      </section>

      <section className="mt-16 grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <FileText className="mb-2 h-5 w-5" aria-hidden="true" />
            <CardTitle>Multi-source data</CardTitle>
          </CardHeader>
          <CardContent>
            <CardDescription>
              SEC filings, earnings transcripts, news, market data, and macro
              indicators — fetched in parallel and cross-validated.
            </CardDescription>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <Newspaper className="mb-2 h-5 w-5" aria-hidden="true" />
            <CardTitle>Cited research memos</CardTitle>
          </CardHeader>
          <CardContent>
            <CardDescription>
              Every numerical claim is tagged with its source. The synthesizer
              downgrades sections that lack evidence rather than speculating.
            </CardDescription>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <Calculator className="mb-2 h-5 w-5" aria-hidden="true" />
            <CardTitle>DCF + comparables</CardTitle>
          </CardHeader>
          <CardContent>
            <CardDescription>
              Pure-Python valuation in Decimal, peer-relative multiples, and a
              5×5 sensitivity grid you can edit in the exported Excel model.
            </CardDescription>
          </CardContent>
        </Card>
      </section>
    </Container>
  );
}
