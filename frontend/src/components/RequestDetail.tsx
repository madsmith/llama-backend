import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import type { RequestLogEntry } from "../api/types";
import JsonTree from "./JsonTree";
import ChatView from "./ChatView";
import EmbeddingsView from "./EmbeddingsView";
import SegmentedControl from "./inputs/SegmentedControl";
import ErrorToast from "./ErrorToast";
import { type PrefCodec, usePref } from "../api/prefs";

/** Stored as "chat" / "raw" (true = the structured view: Chat or Embeddings). */
const chatModePref: PrefCodec<boolean> = {
  decode: (raw) => (raw === "chat" ? true : raw === "raw" ? false : undefined),
  encode: (chat) => (chat ? "chat" : "raw"),
};

/** True when a request body looks like a /v1/embeddings request. */
function isEmbeddingsRequest(body: Record<string, unknown> | null): boolean {
  return body != null && "input" in body && !("messages" in body) && !("prompt" in body);
}

function JsonBlock({ label, data }: { label: string; data: unknown }) {
  const isObject = data !== null && typeof data === "object";
  return (
    <div className="flex flex-col flex-1 min-w-0 min-h-0">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide px-4 py-2 border-b border-gray-800 shrink-0">
        {label}
      </h3>
      <div className="flex-1 overflow-auto p-4">
        {isObject ? (
          <JsonTree data={data} defaultOpen defaultExpandDepth={2} />
        ) : (
          <div className="font-mono text-xs leading-5 text-gray-300 whitespace-pre-wrap break-all">
            {data != null ? String(data) : <span className="text-gray-600">null</span>}
          </div>
        )}
      </div>
    </div>
  );
}

interface Props {
  entry: RequestLogEntry;
  onClose: () => void;
  requestIds?: string[];
}

export default function RequestDetail({ entry: initial, onClose, requestIds = [] }: Props) {
  const [entry, setEntry] = useState(initial);
  const [toast, setToast] = useState<string | null>(null);

  const requestBody = entry.request_body as Record<string, unknown> | null;
  const hasMessages = Array.isArray(requestBody?.messages);
  const structured = hasMessages ? "Chat" : isEmbeddingsRequest(requestBody) ? "Embeddings" : null;
  const [chatMode, setChatMode] = usePref("pref:chatMode", () => structured != null, chatModePref);

  // Reset to the new entry when the parent passes a different one.
  const [prevInitial, setPrevInitial] = useState(initial);
  if (initial !== prevInitial) {
    setPrevInitial(initial);
    setEntry(initial);
  }

  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(id);
  }, [toast]);

  const refresh = useCallback(async () => {
    try {
      const updated = await api.getRequestLog(entry.request_id);
      setEntry(updated);
    } catch {
      setToast("Request no longer in log");
    }
  }, [entry.request_id]);

  const navigate = useCallback(async (dir: -1 | 1) => {
    const idx = requestIds.indexOf(entry.request_id);
    const nextIdx = idx + dir;
    if (idx === -1 || nextIdx < 0 || nextIdx >= requestIds.length) {
      setToast(dir === -1 ? "No previous request" : "No next request");
      return;
    }
    try {
      const next = await api.getRequestLog(requestIds[nextIdx]);
      setEntry(next);
    } catch {
      setToast("Request no longer in log");
    }
  }, [entry.request_id, requestIds]);

  const elapsed =
    entry.elapsed != null ? `${entry.elapsed.toFixed(2)}s` : "pending";
  const status = entry.response_status ?? "pending";
  const streaming = entry.streaming ? " streaming" : "";

  return (
    <div className="absolute inset-0 z-20 flex flex-col bg-gray-900 rounded-b-lg">
      {/* header */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-gray-800 shrink-0">
        <button
          onClick={onClose}
          className="text-sm text-gray-400 hover:text-gray-100 transition font-medium"
        >
          &larr; Back
        </button>
        <span className="text-xs text-gray-600">|</span>
        <button onClick={() => navigate(-1)} className="text-gray-600 hover:text-gray-300 transition text-sm leading-none">&#9664;</button>
        <span className="font-mono text-xs text-gray-400">{entry.request_id}</span>
        <button onClick={() => navigate(1)} className="text-gray-600 hover:text-gray-300 transition text-sm leading-none">&#9654;</button>
        {entry.model_id && (
          <span className="text-xs text-gray-500">{entry.model_id}</span>
        )}
        <span className="text-xs text-gray-500">
          {status}{streaming} &middot; {elapsed}
        </span>
        <div className="flex-1" />
        {structured && (
          <SegmentedControl
            options={["Raw", structured]}
            value={chatMode ? structured : "Raw"}
            onChange={(v) => setChatMode(v !== "Raw")}
          />
        )}
        <button
          onClick={refresh}
          className="text-xs text-gray-500 hover:text-gray-200 transition"
        >
          Refresh
        </button>
      </div>

      {/* pane */}
      <div className="relative flex flex-1 min-h-0 divide-x divide-gray-800">
        {/* The structured/Raw choice is remembered across requests; bodies
            with no structured view (e.g. /v1/systemone) always show raw. */}
        {chatMode && structured && requestBody ? (
          <div className="flex flex-col flex-1 min-w-0 min-h-0">
            {structured === "Chat" ? (
              <ChatView
                key={entry.request_id}
                body={requestBody}
                responseBody={entry.response_body}
                responseStatus={entry.response_status}
              />
            ) : (
              <EmbeddingsView
                body={requestBody}
                responseBody={entry.response_body}
                responseStatus={entry.response_status}
              />
            )}
          </div>
        ) : (
          <>
            <JsonBlock label="Request" data={entry.request_body} />
            <JsonBlock label="Response" data={entry.response_body} />
          </>
        )}

        <ErrorToast message={toast} float />
      </div>
    </div>
  );
}
