import { RenderProse } from "@/components/memo/citation";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Separator } from "@/components/ui/separator";
import type { CitationData, Memo } from "@/lib/api/memo-schema";

interface RisksSectionProps {
  memo: Memo;
  tagMap: Map<string, CitationData>;
}

export function RisksSection({ memo, tagMap }: RisksSectionProps) {
  return (
    <div className="space-y-6">
      <Accordion type="multiple" defaultValue={["bull"]}>
        <AccordionItem value="bull">
          <AccordionTrigger>Bull case</AccordionTrigger>
          <AccordionContent>
            <RenderProse text={memo.bull_case} tagMap={tagMap} />
          </AccordionContent>
        </AccordionItem>
        <AccordionItem value="bear">
          <AccordionTrigger>Bear case</AccordionTrigger>
          <AccordionContent>
            <RenderProse text={memo.bear_case} tagMap={tagMap} />
          </AccordionContent>
        </AccordionItem>
      </Accordion>

      <Separator />

      <section>
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Risks to monitor
        </h3>
        <RenderProse text={memo.risks} tagMap={tagMap} />
      </section>

      <Separator />

      <section>
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Alternative-data signals
        </h3>
        <RenderProse text={memo.alt_data_signals} tagMap={tagMap} />
      </section>
    </div>
  );
}
