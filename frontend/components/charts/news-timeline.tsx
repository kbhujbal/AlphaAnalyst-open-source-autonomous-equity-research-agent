"use client";

import { BarChart } from "@tremor/react";
import { useState } from "react";

import { EmptyChart } from "@/components/charts/empty-chart";
import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

export interface NewsEvent {
  date: string;
  headline: string;
  source: string;
  url: string;
  summary: string | null;
  sentiment: number;
}

interface NewsTimelineProps {
  events: NewsEvent[] | null;
}

interface BarPoint {
  date: string;
  positive: number;
  negative: number;
}

function bucketByDay(events: NewsEvent[]): BarPoint[] {
  const map = new Map<string, BarPoint>();
  for (const ev of events) {
    const day = ev.date.slice(0, 10);
    const cur =
      map.get(day) ?? { date: day, positive: 0, negative: 0 };
    if (ev.sentiment >= 0) cur.positive += ev.sentiment;
    else cur.negative += ev.sentiment;
    map.set(day, cur);
  }
  return Array.from(map.values()).sort((a, b) =>
    a.date.localeCompare(b.date),
  );
}

export function NewsTimeline({ events }: NewsTimelineProps) {
  const [active, setActive] = useState<NewsEvent | null>(null);

  if (!events || events.length === 0) {
    return (
      <EmptyChart
        title="News timeline not available"
        reason="MemoResponse does not yet expose structured news events with sentiment. Once it does, this chart will render the last 90 days as red/green daily bars; clicking a bar opens the article detail."
      />
    );
  }

  const data = bucketByDay(events);
  const eventByDate = new Map<string, NewsEvent[]>();
  for (const ev of events) {
    const day = ev.date.slice(0, 10);
    const arr = eventByDate.get(day) ?? [];
    arr.push(ev);
    eventByDate.set(day, arr);
  }

  return (
    <>
      <BarChart
        data={data}
        index="date"
        categories={["positive", "negative"]}
        colors={["emerald", "rose"]}
        stack
        valueFormatter={(v) => v.toFixed(2)}
        className="h-64"
        aria-label="Daily news sentiment over the last 90 days"
        onValueChange={(v) => {
          // reason: Tremor's EventProps shape is keyed dynamically by the
          // chart's `index` + categories at runtime, so the static type
          // doesn't expose `date`. Narrow through a cast.
          const point = v as { date?: string } | null;
          if (!point || typeof point.date !== "string") return;
          const arr = eventByDate.get(point.date);
          if (arr && arr.length > 0) {
            setActive(arr[0]);
          }
        }}
      />

      <Sheet
        open={active !== null}
        onOpenChange={(open) => {
          if (!open) setActive(null);
        }}
      >
        <SheetContent>
          {active ? (
            <>
              <SheetHeader>
                <SheetTitle>{active.headline}</SheetTitle>
                <SheetDescription>
                  {active.source} · {active.date.slice(0, 10)}
                </SheetDescription>
              </SheetHeader>
              <div className="mt-4 space-y-4 text-sm">
                <Badge variant={active.sentiment >= 0 ? "default" : "destructive"}>
                  Sentiment {active.sentiment >= 0 ? "+" : ""}
                  {active.sentiment.toFixed(2)}
                </Badge>
                {active.summary ? <p>{active.summary}</p> : null}
                <a
                  href={active.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary underline-offset-2 hover:underline"
                >
                  Open article →
                </a>
              </div>
            </>
          ) : null}
        </SheetContent>
      </Sheet>
    </>
  );
}
