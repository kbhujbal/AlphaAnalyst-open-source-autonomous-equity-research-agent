import { RenderProse } from "@/components/memo/citation";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import type { CitationData, Memo } from "@/lib/api/memo-schema";

interface CatalystsSectionProps {
  memo: Memo;
  tagMap: Map<string, CitationData>;
}

const NET_SENT_RE = /net_sentiment=([+\-]?\d+(?:\.\d+)?)/;

function extractSentiment(text: string): number | null {
  const m = NET_SENT_RE.exec(text);
  return m ? Number.parseFloat(m[1]) : null;
}

export function CatalystsSection({ memo, tagMap }: CatalystsSectionProps) {
  const sentiment = extractSentiment(memo.recent_catalysts);
  return (
    <div className="space-y-4">
      {sentiment !== null ? (
        <div className="flex items-center gap-3">
          <span className="text-sm font-medium text-muted-foreground">
            Net news sentiment (90d, recency-weighted)
          </span>
          <Badge
            variant={
              sentiment > 0.1
                ? "default"
                : sentiment < -0.1
                  ? "destructive"
                  : "secondary"
            }
          >
            {sentiment >= 0 ? "+" : ""}
            {sentiment.toFixed(3)}
          </Badge>
        </div>
      ) : null}

      <Separator />

      <section>
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Recent catalysts
        </h3>
        <RenderProse text={memo.recent_catalysts} tagMap={tagMap} />
      </section>

      <p className="text-xs italic text-muted-foreground">
        A per-event table (date / headline / category / impact) will populate
        once the backend exposes structured news classifications in the memo
        payload.
      </p>
    </div>
  );
}
