// GGUF header metadata (from /api/fs/gguf) and how it configures a model.

import { useEffect, useState } from "react";
import { api } from "./client";
import type { ModelAdvanced, ModelConfig } from "./types";

export interface GgufInfo {
  path: string;
  type: "model" | "mmproj" | string;
  architecture: string | null;
  name: string | null;
  context_length: number | null;
  block_count: number | null;
  embedding_length: number | null;
  has_chat_template: boolean;
  tokenizer: { model: string | null; pre: string | null; vocab_size: number | null };
  sampling: Partial<Record<SamplingField, number>>;
  kv_cache: { full_bytes_per_token: number; swa_bytes_per_token: number; sliding_window: number | null } | null;
  projector?: { vision_dim: number | null; audio_dim: number | null };
  projectors?: Array<{ path: string; projector: GgufInfo["projector"] }>;
}

export type SamplingField = "temperature" | "top_k" | "top_p" | "min_p" | "repeat_penalty";

const SAMPLING_LABELS: Record<SamplingField, string> = {
  temperature: "Temperature",
  top_k: "Top K",
  top_p: "Top P",
  min_p: "Min P",
  repeat_penalty: "Repeat Penalty",
};

const DEBOUNCE_MS = 300;

export interface GgufState {
  /** The path this result belongs to (as passed in, not expanded). */
  path: string;
  info: GgufInfo | null;
  error: string | null;
}

/** Reads a GGUF header whenever `path` settles. Empty path → no request. */
export function useGgufInfo(path: string, opts: { projectors?: boolean } = {}): GgufState {
  const projectors = opts.projectors ?? false;
  const [state, setState] = useState<GgufState>({ path: "", info: null, error: null });

  useEffect(() => {
    if (!path.trim()) return;
    let cancelled = false;
    const t = setTimeout(() => {
      api.ggufInfo(path, projectors)
        .then((info) => { if (!cancelled) setState({ path, info, error: null }); })
        .catch((e: unknown) => {
          if (!cancelled) setState({ path, info: null, error: e instanceof Error ? e.message : String(e) });
        });
    }, DEBOUNCE_MS);
    return () => { cancelled = true; clearTimeout(t); };
  }, [path, projectors]);

  // A result for a different (older) path, or no path at all, isn't shown.
  return path.trim() && state.path === path ? state : { path, info: null, error: null };
}

/** Layer count llama-server offloads for "all" (blocks + output layer). */
export function offloadableLayers(info: GgufInfo | null): number | null {
  return info?.block_count != null ? info.block_count + 1 : null;
}

function projectorDim(p: GgufInfo["projector"] | undefined): number[] {
  return [p?.vision_dim, p?.audio_dim].filter((d): d is number => d != null);
}

/** Problem with using `proj` as the multimodal projector for `model`, if any. */
export function projectorProblem(model: GgufInfo | null, proj: GgufInfo | null): string | null {
  if (!proj) return null;
  if (proj.type !== "mmproj") return "This file is not a multimodal projector.";
  const dims = projectorDim(proj.projector);
  if (model?.embedding_length != null && dims.length && !dims.includes(model.embedding_length)) {
    return `Projector output size ${dims.join("/")} doesn't match the model's embedding size ${model.embedding_length}; it was built for a different model.`;
  }
  return null;
}

/** Problem with using `draft` for speculative decoding with `model`, if any. */
export function draftProblem(model: GgufInfo | null, draft: GgufInfo | null): string | null {
  if (!model || !draft) return null;
  if (draft.type !== "model") return "This file is not a language model.";
  const a = model.tokenizer, b = draft.tokenizer;
  const differs = (x: unknown, y: unknown) => x != null && y != null && x !== y;
  if (differs(a.model, b.model) || differs(a.pre, b.pre) || differs(a.vocab_size, b.vocab_size)) {
    return `Vocabulary doesn't match the main model (${describeTokenizer(b)} vs ${describeTokenizer(a)}); speculative decoding needs the same tokenizer.`;
  }
  return null;
}

function describeTokenizer(t: GgufInfo["tokenizer"]): string {
  return [t.pre ?? t.model, t.vocab_size?.toLocaleString()].filter(Boolean).join(", ");
}

/** Approximate f16 KV cache size in bytes for the given context settings. */
export function kvCacheBytes(
  info: GgufInfo | null,
  perSlotCtx: number,
  parallel: number,
  unified: boolean,
  swaFull: boolean,
): number | null {
  const kv = info?.kv_cache;
  if (!kv) return null;
  const totalTokens = unified ? perSlotCtx : perSlotCtx * parallel;
  const swaTokens = swaFull || !kv.sliding_window
    ? totalTokens
    : Math.min(totalTokens, kv.sliding_window * parallel);
  return kv.full_bytes_per_token * totalTokens + kv.swa_bytes_per_token * swaTokens;
}

export function formatBytes(bytes: number): string {
  const gib = bytes / 1024 ** 3;
  return gib >= 1 ? `${gib.toFixed(1)} GiB` : `${Math.round(bytes / 1024 ** 2)} MiB`;
}

export interface ModelFileChanges {
  model: ModelConfig;
  changes: string[];
}

/**
 * Settings to adjust after a model file is picked. Values you entered are
 * kept unless they're out of range for the new model; blanks are filled from
 * the file.
 */
export function applyModelFile(model: ModelConfig, info: GgufInfo): ModelFileChanges {
  const changes: string[] = [];
  const patch: Partial<ModelConfig> = {};
  const adv: Partial<ModelAdvanced> = {};

  const trained = info.context_length;
  if (trained != null && model.ctx_size != null && model.ctx_size > trained) {
    patch.ctx_size = trained;
    changes.push(`Context ${trained.toLocaleString()} (was ${model.ctx_size.toLocaleString()})`);
  }

  const layers = offloadableLayers(info);
  if (layers != null && model.n_gpu_layers > layers) {
    patch.n_gpu_layers = -1;
    changes.push(`GPU layers all (was ${model.n_gpu_layers})`);
  }

  if (!model.advanced.mmproj_path && info.projectors?.length) {
    const match = info.projectors.find((p) =>
      info.embedding_length == null || projectorDim(p.projector).includes(info.embedding_length));
    if (match) {
      adv.mmproj_path = match.path;
      changes.push(`Projector ${match.path.split("/").pop()}`);
    }
  }

  for (const [field, value] of Object.entries(info.sampling) as [SamplingField, number][]) {
    if (model.advanced[field] == null) {
      adv[field] = value;
      changes.push(`${SAMPLING_LABELS[field]} ${value}`);
    }
  }

  return {
    model: { ...model, ...patch, advanced: { ...model.advanced, ...adv } },
    changes,
  };
}
