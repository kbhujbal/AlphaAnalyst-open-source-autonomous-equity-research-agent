"use client";

import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { CitationData } from "@/lib/api/memo-schema";

interface CitationProps {
  id: string;
  citation?: CitationData;
}

export function Citation({ id, citation }: CitationProps) {
  const label = citation
    ? `[${citation.source_type}] ${citation.source_id}`
    : id;
  const trigger = (
    <a
      href={`#cite-${id}`}
      className="ml-0.5 inline-flex align-super"
      aria-label={`Source ${id}`}
    >
      <Badge
        variant="secondary"
        className="h-4 px-1.5 text-[10px] font-medium leading-none"
      >
        {id}
      </Badge>
    </a>
  );
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>{trigger}</TooltipTrigger>
        <TooltipContent className="max-w-sm text-xs">
          <p className="font-medium">{label}</p>
          {citation?.snippet ? (
            <p className="mt-1 text-muted-foreground">
              {citation.snippet.slice(0, 200)}
              {citation.snippet.length > 200 ? "…" : ""}
            </p>
          ) : null}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

const TAG_RE = /\[F\d+\]/g;

interface RenderProseProps {
  text: string;
  tagMap: Map<string, CitationData>;
}

/**
 * Renders prose with `[F\d+]` tokens replaced by interactive Citation badges.
 * Plain text is split into paragraphs on blank lines.
 */
export function RenderProse({ text, tagMap }: RenderProseProps) {
  if (!text || text.trim() === "") {
    return (
      <p className="text-sm italic text-muted-foreground">
        Insufficient evidence
      </p>
    );
  }
  const paragraphs = text.split(/\n\s*\n/).filter((p) => p.trim().length > 0);
  return (
    <div className="space-y-3 text-sm leading-relaxed">
      {paragraphs.map((paragraph, idx) => (
        <p key={idx}>{renderInline(paragraph, tagMap)}</p>
      ))}
    </div>
  );
}

function renderInline(
  text: string,
  tagMap: Map<string, CitationData>,
): ReactNode[] {
  const out: ReactNode[] = [];
  let lastIdx = 0;
  let match: RegExpExecArray | null;
  const re = new RegExp(TAG_RE.source, TAG_RE.flags);
  while ((match = re.exec(text)) !== null) {
    if (match.index > lastIdx) {
      out.push(text.slice(lastIdx, match.index));
    }
    const tag = match[0].slice(1, -1);
    out.push(
      <Citation key={`${match.index}-${tag}`} id={tag} citation={tagMap.get(tag)} />,
    );
    lastIdx = match.index + match[0].length;
  }
  if (lastIdx < text.length) {
    out.push(text.slice(lastIdx));
  }
  return out;
}
