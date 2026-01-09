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
          Financial snapshot (raw)
        </h3>
        <Card>
          <CardContent className="pt-6">
            <RenderProse text={memo.financial_snapshot} tagMap={tagMap} />
          </CardContent>
        </Card>
        <p className="mt-2 text-xs italic text-muted-foreground">
          Structured key-metric tiles will appear once the backend exposes
          per-period revenue / margin / EPS series.
        </p>
      </section>
    </div>
  );
}
