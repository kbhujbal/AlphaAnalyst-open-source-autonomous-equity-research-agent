import { PriceChart } from "@/components/charts/price-chart";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { RenderProse } from "@/components/memo/citation";
import type { CitationData, Memo } from "@/lib/api/memo-schema";

interface SummarySectionProps {
  memo: Memo;
  tagMap: Map<string, CitationData>;
}

export function SummarySection({ memo, tagMap }: SummarySectionProps) {
  return (
    <div className="grid gap-6 md:grid-cols-[1fr_320px]">
      <div className="space-y-6">
        <section>
          <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Executive summary
          </h3>
          <RenderProse text={memo.executive_summary} tagMap={tagMap} />
        </section>

        <Separator />

        <section>
          <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Financial snapshot
          </h3>
          <Card>
            <CardContent className="pt-6">
              <RenderProse text={memo.financial_snapshot} tagMap={tagMap} />
            </CardContent>
          </Card>
        </section>
      </div>
      <aside>
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          1Y price vs SPY
        </h3>
        {/* data sourced from the orchestrator once price_history is exposed */}
        <PriceChart ticker={memo.ticker} data={null} />
      </aside>
    </div>
  );
}
