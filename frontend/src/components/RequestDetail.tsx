import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import type { RequestLogEntry } from "../api/types";
import JsonTree from "./JsonTree";
import ChatView from "./ChatView";

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
}

export default function RequestDetail({ entry: initial, onClose }: Props) {
  const [entry, setEntry] = useState(initial);
  const [toast, setToast] = useState<string | null>(null);

  const requestBody = entry.request_body as Record<string, unknown> | null;
  const hasMessages = Array.isArray(requestBody?.messages);
  const [chatMode, setChatMode] = useState(hasMessages);

  useEffect(() => setEntry(initial), [initial]);

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
        <span className="font-mono text-xs text-gray-400">{entry.request_id}</span>
        {entry.model_id && (
          <span className="text-xs text-gray-500">{entry.model_id}</span>
        )}
        <span className="text-xs text-gray-500">
          {status}{streaming} &middot; {elapsed}
        </span>
        <div className="flex-1" />
        {hasMessages && (
          <div className="flex items-center rounded-full bg-gray-800 p-0.5 gap-0.5">
            {(["Raw", "Chat"] as const).map((mode) => {
              const active = mode === "Chat" ? chatMode : !chatMode;
              return (
                <button
                  key={mode}
                  onClick={() => setChatMode(mode === "Chat")}
                  className={`px-2.5 py-0.5 rounded-full text-xs font-medium transition-colors ${active ? "bg-gray-600 text-gray-100" : "text-gray-500 hover:text-gray-300"}`}
                >
                  {mode}
                </button>
              );
            })}
          </div>
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
        {chatMode && requestBody ? (
          <div className="flex flex-col flex-1 min-w-0 min-h-0">
            <ChatView
              body={requestBody}
              responseBody={entry.response_body}
              responseStatus={entry.response_status}
            />
          </div>
        ) : (
          <>
            <JsonBlock label="Request" data={entry.request_body} />
            <JsonBlock label="Response" data={entry.response_body} />
          </>
        )}

        {/* toast */}
        {toast && (
          <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-30 rounded-md bg-red-900/80 border border-red-700 px-4 py-2 text-sm text-red-200 shadow-lg">
            {toast}
          </div>
        )}
      </div>
    </div>
  );
}
