"use client";

import type { components } from "@/types/api";
import { Citation } from "@/components/memo/citation";

type Record = components["schemas"]["CalculationRecord"];

export function Calculations({ records }: { records: Record[] }) {
  if (!records.length) return null;
  return <section className="space-y-3 rounded-lg border p-4 text-sm">
    <h2 className="font-semibold">Reproducible calculations</h2>
    <p className="text-muted-foreground">Replayed from captured source observations. Scenario assumptions and peer selection require analyst judgment. Ratios are fractions: 0.10 means 10%.</p>
    {records.map(({ trace, sources }) => {
      const nodes = new Map(trace.steps.map(step => [step.node, step]));
      const headline = Object.entries(trace.outputs).filter(([name]) => !name.startsWith("sensitivity:") && !name.startsWith("projected_") && !name.startsWith("present_value_"));
      return <details key={trace.calculation_id} className="border-t pt-3">
        <summary className="cursor-pointer font-medium">{trace.recipe} — {trace.scenario?.name ?? "Reported inputs"}</summary>
        <dl className="mt-2 grid grid-cols-2 gap-2 break-words">{headline.map(([name, node]) => <div key={name}><dt className="text-muted-foreground">{name.replaceAll("_", " ")}</dt><dd>{nodes.get(node)?.value} {nodes.get(node)?.unit}</dd></div>)}</dl>
        {Object.entries(trace.periods ?? {}).length > 0 && <div className="mt-3 space-y-1"><p className="font-medium">Actual trailing windows</p>{Object.entries(trace.periods ?? {}).map(([ticker, period]) => <p key={ticker}>{ticker}: {period.start} to {period.end} ({period.basis})</p>)}<p className="text-muted-foreground">Different fiscal calendars are retained; amounts are not interpolated or calendarized.</p></div>}
        {Object.keys(trace.period_methods ?? {}).length > 0 && <div className="mt-3"><p className="font-medium">Period reconstruction methods</p>{Object.entries(trace.period_methods ?? {}).map(([role, method]) => <p key={role}>{role}: {method}</p>)}<p className="text-muted-foreground">Residuals and roll-forwards are calculated values. Review cross-filing accounting and restatement consistency.</p></div>}
        {trace.scenario && <p className="mt-3">Scenario: WACC {trace.scenario.wacc}; terminal growth {trace.scenario.terminal_growth}; assumed FCFF margin {trace.scenario.fcff_margin}; horizon {trace.scenario.projection_years} years. {trace.scenario.rationale}</p>}
        {(trace.warnings ?? []).map(warning => <p key={warning} className="mt-2 text-muted-foreground">{warning}</p>)}
        <a className="mt-3 inline-block underline" download={`${trace.ticker}-${trace.recipe}-${trace.calculation_id.slice(0, 8)}.json`} href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify({ trace, sources }, null, 2))}`}>Download trace JSON</a>
        <div className="mt-3 space-y-2">{sources.map((source, index) => <div key={source.observation.observation_id} className="break-all">
          {source.observation.ticker} {source.observation.metric}: {source.observation.value} {source.observation.unit}; {source.observation.period.start ?? "as of"} — {source.observation.period.end}
          <Citation id={`Input ${index + 1}`} citation={{ source_type: "fact", source_id: source.observation.source.url, snippet: source.passages.map(p => p.text).join("\n\n"), passages: source.passages, observation_id: source.observation.observation_id }} />
          {source.observation.market_cap_context && <p className="text-xs text-muted-foreground">Quote currency from current listing profile captured {source.observation.market_cap_context.profile_retrieved_at}; limited to recent market dates. This is separate from financial reporting currency.</p>}
          <p className="text-xs text-muted-foreground">{source.observation.observation_id}</p>
        </div>)}</div>
        <details className="mt-3"><summary className="cursor-pointer">Formula steps and sensitivity cells ({trace.steps.length})</summary>
          <div className="mt-2 overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr><th>Node</th><th>Formula / inputs</th><th>Exact value</th><th>Unit</th></tr></thead>
            <tbody>{trace.steps.map(step => <tr key={step.node} className="border-t align-top"><td className="p-2">{step.node}</td><td className="p-2">{step.operation}({(step.inputs ?? []).join(", ")}){step.note && <p>{step.note}</p>}</td><td className="p-2">{step.value}</td><td className="p-2">{step.unit}</td></tr>)}</tbody></table></div>
        </details>
        <p className="mt-2 break-all text-xs text-muted-foreground">{trace.engine}; identity {trace.calculation_id}</p>
      </details>;
    })}
  </section>;
}
