"use client";

import { Badge } from "@/components/ui/badge";
import { ErrorState } from "@/components/ui/error-state";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import type { MemoResponse } from "@/lib/api/client";
import { MemoZ, buildTagMap } from "@/lib/api/memo-schema";

import { ExportButtons } from "./export-buttons";
import { CatalystsSection } from "./sections/catalysts-section";
import { FinancialsSection } from "./sections/financials-section";
import { RisksSection } from "./sections/risks-section";
import { SourcesSection } from "./sections/sources-section";
import { SummarySection } from "./sections/summary-section";
import { ValuationSection } from "./sections/valuation-section";

interface MemoViewProps {
  response: MemoResponse;
}

function absoluteApiUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  return `${base.replace(/\/$/, "")}${path}`;
}

export function MemoView({ response }: MemoViewProps) {
  const parsed = MemoZ.safeParse(response.sections);
  if (!parsed.success) {
    return (
      <ErrorState
        title="Memo schema mismatch"
        description="The backend returned a memo in an unexpected shape. Run `npm run codegen` to regenerate types/api.ts and rebuild the frontend."
      />
    );
  }
  const memo = parsed.data;
  const tagMap = buildTagMap(memo);
  const generatedAt = new Date(response.generated_at).toLocaleString();
  // cost_usd serializes from a Decimal on the backend, so it's a string here.
  const costParsed = Number.parseFloat(response.cost_usd);
  const costFormatted = Number.isFinite(costParsed)
    ? costParsed.toFixed(4)
    : response.cost_usd;

  return (
    <div className="grid gap-8 lg:grid-cols-[1fr_280px]">
      <div className="min-w-0">
        <header className="mb-6 flex flex-wrap items-baseline gap-3">
          <h1 className="text-3xl font-bold tracking-tight">
            {response.ticker}
          </h1>
          <Badge variant="outline" className="text-xs">
            Memo · {memo.as_of}
          </Badge>
        </header>

        <Tabs defaultValue="summary">
          <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
            <TabsList className="inline-flex w-max">
              <TabsTrigger value="summary">Summary</TabsTrigger>
              <TabsTrigger value="financials">Financials</TabsTrigger>
              <TabsTrigger value="catalysts">Catalysts</TabsTrigger>
              <TabsTrigger value="valuation">Valuation</TabsTrigger>
              <TabsTrigger value="risks">Risks</TabsTrigger>
              <TabsTrigger value="sources">Sources</TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="summary" className="pt-6">
            <SummarySection memo={memo} tagMap={tagMap} />
          </TabsContent>
          <TabsContent value="financials" className="pt-6">
            <FinancialsSection memo={memo} tagMap={tagMap} />
          </TabsContent>
          <TabsContent value="catalysts" className="pt-6">
            <CatalystsSection memo={memo} tagMap={tagMap} />
          </TabsContent>
          <TabsContent value="valuation" className="pt-6">
            <ValuationSection memo={memo} tagMap={tagMap} />
          </TabsContent>
          <TabsContent value="risks" className="pt-6">
            <RisksSection memo={memo} tagMap={tagMap} />
          </TabsContent>
          <TabsContent value="sources" className="pt-6">
            <SourcesSection memo={memo} tagMap={tagMap} />
          </TabsContent>
        </Tabs>
      </div>

      <aside className="space-y-4 lg:sticky lg:top-4 lg:self-start">
        <div className="space-y-3 rounded-lg border bg-card p-4 text-sm">
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground">
              Generated
            </div>
            <div className="font-medium">{generatedAt}</div>
          </div>
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground">
              Cost
            </div>
            <div className="font-medium">${costFormatted}</div>
          </div>
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground">
              Sources
            </div>
            <div className="font-medium">{memo.citations.length}</div>
          </div>
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground">
              LLM calls
            </div>
            <div className="font-medium">{response.llm_calls}</div>
          </div>
        </div>
        <ExportButtons
          pdfHref={absoluteApiUrl(response.exports.pdf)}
          excelHref={absoluteApiUrl(response.exports.excel)}
        />
      </aside>
    </div>
  );
}
