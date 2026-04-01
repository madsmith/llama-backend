import { useEffect, useRef, useState, useMemo, useCallback } from "react";
import { TabButton, PANEL_BG } from "./TabBar";
import { api } from "../api/client";
import type { RequestLogEntry } from "../api/types";
import type { LogLine } from "../api/hooks";
import RequestDetail from "./RequestDetail";

type Tab = "server" | "info" | "other" | "all";

const SRV_RE = /\bsrv\b/;
const API_CALL_RE = /srv.*(?:GET \/slots|update_slots)/;
const INFO_RE = /print_info/;
const CMD_RE = /^\$ /;
const MAIN_RE = /\bmain: /;
const ALWAYS_RE = (l: string) => CMD_RE.test(l) || MAIN_RE.test(l);

function filterLines(lines: LogLine[], tab: Tab, showApiCalls: boolean): LogLine[] {
  if (tab === "all") return lines;
  if (tab === "info") return lines.filter((l) => INFO_RE.test(l.text) || ALWAYS_RE(l.text));
  if (tab === "other") return lines.filter((l) => !SRV_RE.test(l.text));
  return lines.filter((l) => {
    if (ALWAYS_RE(l.text)) return true;
    if (!SRV_RE.test(l.text)) return false;
    if (!showApiCalls && API_CALL_RE.test(l.text)) return false;
    return true;
  });
}

interface Props {
  lines: LogLine[];
  connected: boolean;
  onClear: () => void;
  source?: string;
  isPending?: boolean;
}

export default function LogViewer({ lines, connected, onClear, source, isPending }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollLockedRef = useRef(false);
  const lockTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [tab, setTab] = useState<Tab>("server");
  const [showApiCalls, setShowApiCalls] = useState(false);
  const [selectedEntry, setSelectedEntry] = useState<RequestLogEntry | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const isProxy = source === "proxy";

  const filtered = useMemo(
    () => (isProxy ? lines : filterLines(lines, tab, showApiCalls)),
    [lines, tab, showApiCalls, isProxy],
  );

  const handleScroll = useCallback(() => {
    scrollLockedRef.current = true;
    if (lockTimerRef.current !== null) clearTimeout(lockTimerRef.current);
    lockTimerRef.current = setTimeout(() => {
      scrollLockedRef.current = false;
      lockTimerRef.current = null;
    }, 3000);
  }, []);

  useEffect(() => {
    if (!selectedEntry && !scrollLockedRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [filtered.length, selectedEntry]);

  // Auto-dismiss toast
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(id);
  }, [toast]);

  const handleLineClick = useCallback(async (requestId: string) => {
    try {
      const entry = await api.getRequestLog(requestId);
      setSelectedEntry(entry);
    } catch {
      setToast("Request log entry not found");
    }
  }, []);

  return (
    <div className="flex flex-col h-full">
      {/* tabs */}
      <div className="flex items-end gap-1.5">
        {isProxy ? (
          <TabButton label="All" active onClick={() => {}} />
        ) : (
          <>
            <TabButton label="Server" active={tab === "server"} onClick={() => setTab("server")} />
            <TabButton label="Info" active={tab === "info"} onClick={() => setTab("info")} />
            <TabButton label="Other" active={tab === "other"} onClick={() => setTab("other")} />
            <div className="flex-1" />
            <TabButton label="All" active={tab === "all"} onClick={() => setTab("all")} />
          </>
        )}
      </div>

      {/* panel */}
      <div className={`relative flex flex-col flex-1 min-h-0 ${PANEL_BG} rounded-b-lg`}>
        {/* toolbar area */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
          <div className="flex items-center gap-4">
            {!isProxy && tab === "server" && (
              <label className="flex items-center gap-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={showApiCalls}
                  onChange={(e) => setShowApiCalls(e.target.checked)}
                  className="h-3.5 w-3.5 rounded border-gray-700 bg-gray-800 accent-blue-500"
                />
                <span className="text-xs text-gray-500">Display API calls</span>
              </label>
            )}
          </div>
          <div className="flex items-center gap-4">
            <span className="text-xs text-gray-600">
              {connected ? "Connected" : "Disconnected"} &middot; {filtered.length} lines
            </span>
            <button
              onClick={onClear}
              className="text-xs text-gray-600 hover:text-gray-300 transition"
            >
              Clear
            </button>
          </div>
        </div>

        {/* log output */}
        <div className="flex-1 overflow-auto p-4 font-mono text-xs leading-5 text-gray-300" onScroll={handleScroll}>
          {isPending && lines.length === 0 && (
            <span className="text-gray-600">Loading logs...</span>
          )}
          {!isPending && filtered.length === 0 && (
            <span className="text-gray-600">No log output yet.</span>
          )}
          {filtered.map((l) =>
            l.request_id ? (
              <div
                key={l.id}
                className="whitespace-pre-wrap break-all cursor-pointer hover:bg-gray-800/60 rounded px-1 -mx-1 transition-colors"
                onClick={() => handleLineClick(l.request_id!)}
              >
                {l.text}
              </div>
            ) : (
              <div key={l.id} className="whitespace-pre-wrap break-all">
                {l.text}
              </div>
            ),
          )}
          <div ref={bottomRef} />
        </div>

        {/* request detail overlay */}
        {selectedEntry && (
          <RequestDetail entry={selectedEntry} onClose={() => setSelectedEntry(null)} />
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
