"use client";

import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogTrigger } from "@/components/ui/dialog";
import type { CitationData } from "@/lib/api/memo-schema";
import { evidencePath } from "@/lib/api/evidence-link";
import { apiBaseUrl } from "@/lib/api/client";

interface CitationProps { id: string; citation?: CitationData; }

export function Citation({ id, citation }: CitationProps) {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <button type="button" className="ml-0.5 inline-flex align-super" aria-label={`Inspect source ${id}`}>
          <Badge variant="secondary" className="h-4 px-1.5 text-[10px] font-medium leading-none">{id}</Badge>
        </button>
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Source {id}</DialogTitle>
          <DialogDescription>Inspect the captured evidence. Quotes establish what the retained source says; interpretations still require analyst review.</DialogDescription>
        </DialogHeader>
        {(citation?.passages ?? []).length ? citation!.passages!.map((passage, index) => (
          <section key={`${passage.document_id}-${passage.start}-${index}`} className="space-y-2 border-t pt-3 text-sm">
            <p className="font-medium">{passage.scope === "feed_excerpt" ? "Feed excerpt — full article not captured" : passage.scope === "normalized_filing" ? "Captured normalized filing text — not PDF page numbers" : passage.scope === "provider_record" ? "Captured provider JSON record" : "Captured transcript"}</p>
            <blockquote className="whitespace-pre-wrap break-words rounded bg-muted p-3">{passage.text}</blockquote>
            <a href={passage.source_url} target="_blank" rel="noopener noreferrer" className="underline">Open source</a>
            {evidencePath(passage.snapshot_url) ? <a href={`${apiBaseUrl}${evidencePath(passage.snapshot_url)}`} target="_blank" rel="noopener noreferrer" className="ml-4 underline">Open captured source JSON</a> : null}
            <dl className="break-all text-xs text-muted-foreground">
              <dt>Unicode character offsets (end exclusive)</dt><dd>{passage.start}–{passage.end}</dd>
              <dt>Document version</dt><dd>{passage.document_id}</dd>
              <dt>Document SHA-256</dt><dd>{passage.document_hash}</dd>
              <dt>Passage SHA-256</dt><dd>{passage.text_hash}</dd>
              <dt>Retrieved</dt><dd>{passage.retrieved_at}</dd>
            </dl>
          </section>
        )) : <p>No retained source passage is available. This legacy citation is unverified.</p>}
      </DialogContent>
    </Dialog>
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
