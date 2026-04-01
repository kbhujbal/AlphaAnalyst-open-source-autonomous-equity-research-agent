import { FinancialsChart } from "@/components/charts/financials-chart";
import { PriceChart } from "@/components/charts/price-chart";
import { RenderProse } from "@/components/memo/citation";
import { Separator } from "@/components/ui/separator";
import type { CitationData, Memo } from "@/lib/api/memo-schema";

interface FinancialsSectionProps {
  memo: Memo;
  tagMap: Map<string, CitationData>;
}

export function FinancialsSection({ memo, tagMap }: FinancialsSectionProps) {
  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Annual financials
        </h3>
        <FinancialsChart data={null} />
      </section>

      <Separator />

      <section>
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Synthesizer prose
        </h3>
        <RenderProse text={memo.financial_snapshot} tagMap={tagMap} />
      </section>

      <Separator />

      <section>
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Price history
        </h3>
        <PriceChart ticker={memo.ticker} data={null} />
      </section>
    </div>
  );
}
