import { Calculator, FileText, Newspaper } from "lucide-react";
import Link from "next/link";

import { TickerSearch } from "@/components/analysis/ticker-search";
import { Container } from "@/components/layout/container";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";

const HOW_IT_WORKS = [
  {
    step: 1,
    title: "Fetch",
    body: "SEC filings, transcripts, prices, news, macro, and analyst estimates pulled in parallel.",
  },
  {
    step: 2,
    title: "Analyze",
    body: "Specialist agents draft findings and a skeptical review step challenges the narrative.",
  },
  {
    step: 3,
    title: "Value",
    body: "Source-linked growth, margins and valuation recipes; explicit scenario assumptions require analyst review.",
  },
  {
    step: 4,
    title: "Cite",
    body: "Inspect retained passages and replay numerical calculations. Interpretations require analyst review.",
  },
];

export default function HomePage() {
  return (
    <Container className="py-12">
      <section className="mx-auto max-w-3xl text-center">
        <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
          AlphaAnalyst
        </h1>
        <p className="mt-4 text-lg text-muted-foreground">
          Open-source equity research prototype. Given a US stock ticker,
          draft a research memo with experimental valuation, news analysis, and source
          references.
        </p>
        <div className="mt-8">
          <TickerSearch />
        </div>
        <p className="mt-4 text-sm text-muted-foreground">
          <Link href="/historical" className="font-medium text-foreground underline underline-offset-2">Analyze historical financials</Link> or browse a{" "}
          <Link
            href="/demo"
            className="font-medium text-foreground underline-offset-2 hover:underline"
          >
            fictional offline demo
          </Link>
          .
        </p>
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
              indicators — fetched in parallel, with limited financial cross-checks.
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
              Source references accompany findings. Missing coverage and processing
              failures are disclosed; analysts must verify the underlying claims.
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
              Experimental Python valuation and peer comparisons. Excel exports
              are snapshots; changing inputs does not recalculate the model.
            </CardDescription>
          </CardContent>
        </Card>
      </section>

      <Separator className="my-16" />

      <section>
        <h2 className="mb-6 text-center text-2xl font-semibold tracking-tight">
          How it works
        </h2>
        <ol className="grid gap-4 md:grid-cols-4">
          {HOW_IT_WORKS.map(({ step, title, body }) => (
            <li key={step} className="rounded-lg border bg-card p-5">
              <div className="mb-2 inline-flex h-7 w-7 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">
                {step}
              </div>
              <h3 className="font-medium">{title}</h3>
              <p className="mt-1 text-sm text-muted-foreground">{body}</p>
            </li>
          ))}
        </ol>
      </section>
    </Container>
  );
}
