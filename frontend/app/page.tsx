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
    body: "Five LLM agents extract claims and a Devil's Advocate (different model) argues the bear case.",
  },
  {
    step: 3,
    title: "Value",
    body: "Pure-Python DCF in Decimal + peer-comparable multiples + a 5×5 sensitivity grid.",
  },
  {
    step: 4,
    title: "Cite",
    body: "Every numerical claim in the memo carries a [source] tag the synthesizer validates.",
  },
];

// Sample memo for the public homepage. The real demo job_id will be created
// in Phase 17's eval suite — until then this link is a placeholder.
const SAMPLE_JOB_ID = "00000000-0000-0000-0000-000000000000";

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
          <TickerSearch />
        </div>
        <p className="mt-4 text-sm text-muted-foreground">
          Or browse a{" "}
          <Link
            href={`/analysis/${SAMPLE_JOB_ID}`}
            className="font-medium text-foreground underline-offset-2 hover:underline"
          >
            sample memo
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
