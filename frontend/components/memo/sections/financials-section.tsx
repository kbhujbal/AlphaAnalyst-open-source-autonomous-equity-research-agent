import { RenderProse } from "@/components/memo/citation";
import type { CitationData, Memo } from "@/lib/api/memo-schema";

interface FinancialsSectionProps {
  memo: Memo;
  tagMap: Map<string, CitationData>;
}

export function FinancialsSection({ memo, tagMap }: FinancialsSectionProps) {
  return (
    <div className="space-y-4">
      <RenderProse text={memo.financial_snapshot} tagMap={tagMap} />
      <p className="text-xs italic text-muted-foreground">
        A multi-year table (revenue / gross / op-income / net / FCF) will
        render here once the orchestrator persists per-period XBRL facts in
        the response. For now the synthesizer&apos;s prose is rendered above with
        every cited number linkable to the Sources tab.
      </p>
    </div>
  );
}
