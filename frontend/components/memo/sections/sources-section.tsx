import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { CitationData, Memo } from "@/lib/api/memo-schema";

interface SourcesSectionProps {
  memo: Memo;
  tagMap: Map<string, CitationData>;
}

const TYPE_COLOR: Record<CitationData["source_type"], string> = {
  filing: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
  transcript: "bg-purple-500/10 text-purple-600 dark:text-purple-400",
  news: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  fact: "bg-slate-500/10 text-slate-600 dark:text-slate-400",
  price: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  macro: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
  estimates: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400",
};

function isHttpUrl(value: string): boolean {
  return value.startsWith("http://") || value.startsWith("https://");
}

export function SourcesSection({ memo, tagMap }: SourcesSectionProps) {
  if (memo.citations.length === 0) {
    return (
      <p className="text-sm italic text-muted-foreground">
        No citations were emitted for this memo.
      </p>
    );
  }

  // Invert tagMap so we can label each citation row with its F-tag (matches
  // the badge users see inline in other tabs).
  const tagByCitation = new Map<CitationData, string>();
  for (const [tag, citation] of tagMap.entries()) {
    tagByCitation.set(citation, tag);
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-12">#</TableHead>
          <TableHead className="w-20">Tag</TableHead>
          <TableHead className="w-28">Type</TableHead>
          <TableHead>Source</TableHead>
          <TableHead>Snippet</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {memo.citations.map((citation, idx) => {
          const tag = tagByCitation.get(citation) ?? `F${idx + 1}`;
          return (
            <TableRow key={`${tag}-${idx}`} id={`cite-${tag}`}>
              <TableCell className="text-muted-foreground">{idx + 1}</TableCell>
              <TableCell>
                <Badge variant="secondary">{tag}</Badge>
              </TableCell>
              <TableCell>
                <Badge className={TYPE_COLOR[citation.source_type]} variant="outline">
                  {citation.source_type}
                </Badge>
              </TableCell>
              <TableCell className="max-w-[24ch] truncate text-xs">
                {isHttpUrl(citation.source_id) ? (
                  <a
                    href={citation.source_id}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline-offset-2 hover:underline"
                  >
                    {citation.source_id}
                  </a>
                ) : (
                  <span>{citation.source_id}</span>
                )}
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {citation.snippet.length > 200
                  ? `${citation.snippet.slice(0, 200)}…`
                  : citation.snippet}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
