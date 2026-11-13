// Structured view of a /v1/embeddings exchange: each input next to its
// vector's size, L2 norm and leading values, and a cosine similarity grid
// when there are several. The raw JSON is mostly thousands of floats.

import type { CSSProperties } from "react";
import VectorLink from "./VectorDialog";

interface Item {
  label: string;
  vector: number[] | null;
  /** Shown instead of values when the vector isn't a float array (base64). */
  note?: string;
}

/** Inputs may be a string, strings, token ids, or arrays of token ids. */
function inputLabels(input: unknown): string[] {
  const one = (v: unknown): string =>
    typeof v === "string" ? v
      : Array.isArray(v) ? `[${v.length} tokens]`
      : JSON.stringify(v);
  if (typeof input === "string") return [input];
  if (Array.isArray(input)) {
    if (input.length > 0 && input.every((v) => typeof v === "number")) return [one(input)];
    return input.map(one);
  }
  return input == null ? [] : [one(input)];
}

function itemsOf(body: Record<string, unknown>, response: unknown): Item[] {
  const labels = inputLabels(body.input);
  const data = (response as { data?: unknown } | null)?.data;
  const rows = Array.isArray(data) ? (data as Array<{ index?: number; embedding?: unknown }>) : [];
  const byIndex = new Map(rows.map((r, i) => [typeof r.index === "number" ? r.index : i, r.embedding]));
  const n = Math.max(labels.length, rows.length);
  return Array.from({ length: n }, (_, i) => {
    const e = byIndex.get(i);
    const label = labels[i] ?? `(input ${i})`;
    if (Array.isArray(e)) {
      // llama-server returns one vector per token when pooling is none.
      if (e.length > 0 && Array.isArray(e[0])) {
        return { label, vector: null, note: `${e.length} vectors (no pooling) of ${(e[0] as unknown[]).length} dims` };
      }
      return { label, vector: e as number[] };
    }
    if (typeof e === "string") return { label, vector: null, note: `base64, ${e.length} chars` };
    return { label, vector: null, note: rows.length ? "missing" : undefined };
  });
}

function norm(v: number[]): number {
  let s = 0;
  for (const x of v) s += x * x;
  return Math.sqrt(s);
}

function cosine(a: number[], b: number[]): number | null {
  if (a.length !== b.length || a.length === 0) return null;
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  return na && nb ? dot / Math.sqrt(na * nb) : null;
}

/** Cells from transparent (0 or below) to strong blue (1). */
function cellStyle(sim: number): CSSProperties {
  const t = Math.max(0, Math.min(1, sim));
  return { backgroundColor: `rgba(59, 130, 246, ${(0.08 + 0.62 * t).toFixed(3)})` };
}

const MAX_GRID = 24;

export default function EmbeddingsView({ body, responseBody, responseStatus }: {
  body: Record<string, unknown>;
  responseBody?: unknown;
  responseStatus?: number | null;
}) {
  const model = typeof body.model === "string" ? body.model : "";
  const items = itemsOf(body, responseBody);
  const vectors = items.map((it) => it.vector);
  const dims = vectors.find((v) => v)?.length;
  const usage = (responseBody as { usage?: { prompt_tokens?: number } } | null)?.usage;
  const failed = responseStatus != null && responseStatus >= 400;
  const gridable = vectors.filter(Boolean).length >= 2 && items.length <= MAX_GRID;

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center gap-2 px-4 py-2 shrink-0 border-b border-gray-800 bg-gray-800/20 text-xs">
        <span className="text-gray-300 font-mono">{model}</span>
        <span className="text-gray-600">&middot;</span>
        <span className="text-gray-500">{items.length} {items.length === 1 ? "input" : "inputs"}</span>
        {dims != null && (
          <>
            <span className="text-gray-600">&middot;</span>
            <span className="text-gray-500">{dims} dims</span>
          </>
        )}
        {usage?.prompt_tokens != null && (
          <>
            <span className="text-gray-600">&middot;</span>
            <span className="text-gray-500">{usage.prompt_tokens} tokens</span>
          </>
        )}
      </div>

      <div className="flex-1 overflow-auto px-4 py-4 space-y-6">
        {failed && (
          <div className="rounded-lg border border-red-900/60 bg-red-950/30 px-4 py-3">
            <div className="text-[10px] font-bold uppercase tracking-wider text-red-400 mb-1.5">
              error {responseStatus}
            </div>
            <div className="text-xs text-gray-300 leading-5 font-mono whitespace-pre-wrap break-all">
              {typeof responseBody === "string" ? responseBody : JSON.stringify(responseBody, null, 2)}
            </div>
          </div>
        )}

        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-gray-500 border-b border-gray-800">
              <th className="py-1.5 pr-3 font-medium w-8">#</th>
              <th className="py-1.5 pr-3 font-medium">Input</th>
              <th className="py-1.5 pr-3 font-medium text-right w-20">Norm</th>
              <th className="py-1.5 font-medium">Values</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it, i) => (
              <tr key={i} className="border-b border-gray-800/60 align-top">
                <td className="py-2 pr-3 text-gray-500 font-mono">{i}</td>
                <td className="py-2 pr-3 text-gray-200 whitespace-pre-wrap break-words">
                  <div className="line-clamp-4" title={it.label}>{it.label}</div>
                </td>
                <td className="py-2 pr-3 text-right font-mono text-gray-400">
                  {it.vector ? norm(it.vector).toFixed(4) : ""}
                </td>
                <td className="py-2 font-mono text-gray-500 whitespace-nowrap">
                  {it.vector ? (
                    <>
                      <VectorLink vector={it.vector} title={`Embedding ${i}`} />{" "}
                      <span className="text-gray-600">
                        {it.vector.slice(0, 3).map((x) => x.toFixed(3)).join(", ")}
                        {it.vector.length > 3 ? ", ..." : ""}
                      </span>
                    </>
                  ) : (
                    it.note ?? (failed ? "" : "pending")
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {gridable && (
          <div>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">
              Cosine similarity
            </h3>
            <table className="text-xs font-mono">
              <thead>
                <tr>
                  <th />
                  {items.map((_, j) => (
                    <th key={j} className="px-1 pb-1 text-gray-500 font-normal w-14">{j}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map((_, i) => (
                  <tr key={i}>
                    <th className="pr-2 text-gray-500 font-normal text-right">{i}</th>
                    {items.map((_, j) => {
                      const a = vectors[i], b = vectors[j];
                      const sim = a && b ? cosine(a, b) : null;
                      return (
                        <td
                          key={j}
                          className="w-14 h-7 text-center text-gray-200 border border-gray-900"
                          style={sim != null ? cellStyle(sim) : undefined}
                          title={sim != null ? `${i} vs ${j}: ${sim.toFixed(4)}` : undefined}
                        >
                          {sim != null ? sim.toFixed(2) : ""}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
