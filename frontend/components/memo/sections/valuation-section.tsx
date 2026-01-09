import { RenderProse } from "@/components/memo/citation";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import type { CitationData, Memo } from "@/lib/api/memo-schema";

interface ValuationSectionProps {
  memo: Memo;
  tagMap: Map<string, CitationData>;
}

export function ValuationSection({ memo, tagMap }: ValuationSectionProps) {
  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Valuation narrative
        </h3>
        <Card>
          <CardContent className="pt-6">
            <RenderProse text={memo.valuation} tagMap={tagMap} />
          </CardContent>
        </Card>
      </section>

      <Separator />

      <section>
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Earnings call tone shift
        </h3>
        <RenderProse text={memo.earnings_call_tone_shift} tagMap={tagMap} />
      </section>

      <p className="text-xs italic text-muted-foreground">
        DCF method comparison and the 5×5 sensitivity heatmap render in Phase
        16 (chart phase). The Excel export from the right rail already carries
        a live, editable DCF model with sensitivity grid.
      </p>
    </div>
  );
}
