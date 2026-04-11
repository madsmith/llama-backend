import { useEffect, useRef, useState, useMemo, useCallback } from "react";
import { TabButton, PANEL_BG } from "./TabBar";
import { api } from "../api/client";
import type { RequestLogEntry } from "../api/types";
import type { LogLine, WireProxyRequest, WireProxyResponse } from "../api/hooks";
import RequestDetail from "./RequestDetail";
import ToggleSelector from "./inputs/ToggleSelector";

type Tab = "server" | "info" | "other" | "all";

const SRV_RE = /\bsrv\b/;
const API_CALL_RE = /srv.*(?:GET \/slots|update_slots)/;
const INFO_RE = /print_info/;
const CMD_RE = /^\$ /;
const MAIN_RE = /\bmain: /;
const ALWAYS_RE = (s: string) => CMD_RE.test(s) || MAIN_RE.test(s);

type RequestGroup = {
  kind: "group";
  request_id: string;
  req: WireProxyRequest;
  startTime: number;
  res: WireProxyResponse | null;
  endTime: number | null;
};

type DisplayItem = { kind: "line"; line: LogLine } | RequestGroup;

function lineText(l: LogLine): string {
  if (l.data.type === "text") return l.data.text;
  if (l.data.type === "request") {
    const d = l.data;
    const route = d.server_name ? `[${d.server_name}] ` : "";
    let msg = `\u2192 ${route}${d.method} ${d.path} HTTP/${d.http_ver}`;
    if (d.size != null) msg += ` [${fmtSize(d.size)}]`;
    return `[${fmtTime(l.time)}] ${msg}`;
  }
  const d = l.data;
  const route = d.server_name ? `[${d.server_name}] ` : "";
  let msg: string;
  if (d.complete) {
    msg = `stream complete (${d.elapsed?.toFixed(2)}s) [${fmtSize(d.size ?? 0)}]`;
  } else {
    msg = `HTTP/${d.http_ver} ${d.status}${d.phrase ? ` ${d.phrase}` : ""}`;
    if (d.streaming) msg += " streaming";
    if (d.elapsed != null) msg += ` (${d.elapsed.toFixed(2)}s)`;
    if (d.size != null) msg += ` [${fmtSize(d.size)}]`;
  }
  return `[${fmtTime(l.time)}] \u2190 ${route}${msg}`;
}

function fmtTime(t: number): string {
  return new Date(t * 1000).toLocaleTimeString("en-US", { hour12: false });
}

function fmtSize(n: number): string {
  return n < 1024 ? `${n}B` : `${(n / 1024).toFixed(1)}KB`;
}

function reqSummary(req: WireProxyRequest): string {
  const route = req.server_name ? `[${req.server_name}] ` : "";
  let msg = `${route}${req.method} ${req.path} HTTP/${req.http_ver}`;
  if (req.size != null) msg += ` [${fmtSize(req.size)}]`;
  return msg;
}

function resSummary(res: WireProxyResponse): string {
  const route = res.server_name ? `[${res.server_name}] ` : "";
  let msg: string;
  if (res.complete) {
    msg = `stream complete (${res.elapsed?.toFixed(2)}s) [${fmtSize(res.size ?? 0)}]`;
  } else {
    msg = `HTTP/${res.http_ver} ${res.status}${res.phrase ? ` ${res.phrase}` : ""}`;
    if (res.streaming) msg += " streaming";
    if (res.elapsed != null) msg += ` (${res.elapsed.toFixed(2)}s)`;
    if (res.size != null) msg += ` [${fmtSize(res.size)}]`;
  }
  return `${route}${msg}`;
}

function lineTimeParts(l: LogLine): [string, string] {
  const full = lineText(l);
  const m = /^(\[\d{2}:\d{2}:\d{2}\]) (.*)$/s.exec(full);
  return m ? [m[1], m[2]] : ["", full];
}

function filterLines(lines: LogLine[], tab: Tab, showApiCalls: boolean): LogLine[] {
  if (tab === "all") return lines;
  return lines.filter((l) => {
    const text = lineText(l);
    if (tab === "info") return INFO_RE.test(text) || ALWAYS_RE(text);
    if (tab === "other") return !SRV_RE.test(text);
    // server tab
    if (ALWAYS_RE(text)) return true;
    if (!SRV_RE.test(text)) return false;
    if (!showApiCalls && API_CALL_RE.test(text)) return false;
    return true;
  });
}

function FlatEntry({ line, hoveredLineId, hoveredRequestId, onClick, onEnter, onLeave }: {
  line: LogLine;
  hoveredLineId: string | null;
  hoveredRequestId: string | null;
  onClick: (requestId: string) => void;
  onEnter: (lineId: string, requestId: string) => void;
  onLeave: () => void;
}) {
  const isHovered = line.id === hoveredLineId;
  const isRelated = !isHovered && line.request_id != null && line.request_id === hoveredRequestId;
  const bg = isHovered ? "bg-gray-600/70" : isRelated ? "bg-gray-700/30" : "hover:bg-gray-600/70";
  return line.request_id ? (
    <div
      className={`whitespace-pre-wrap break-all cursor-pointer ${bg} rounded px-1 -mx-1 transition-colors`}
      onClick={() => onClick(line.request_id!)}
      onMouseEnter={() => onEnter(line.id, line.request_id!)}
      onMouseLeave={onLeave}
    >
      {lineText(line)}
    </div>
  ) : (
    <div className={`whitespace-pre-wrap break-all ${bg} rounded px-1 -mx-1`}>{lineText(line)}</div>
  );
}

function GridGroupEntry({ group, isHovered, onClick, onEnter, onLeave }: {
  group: RequestGroup;
  isHovered: boolean;
  onClick: () => void;
  onEnter: () => void;
  onLeave: () => void;
}) {
  const bg = isHovered ? "bg-gray-600/70" : "";
  const cell = `cursor-pointer ${bg} px-1 transition-colors`;
  const timeSpan = `[${fmtTime(group.startTime)} - ${group.endTime ? fmtTime(group.endTime) : "?"}]`;
  return (
    <>
      <span className={`${cell} -ml-1 rounded-l text-gray-500`} onClick={onClick} onMouseEnter={onEnter} onMouseLeave={onLeave}>{timeSpan}</span>
      <span className={cell} onClick={onClick} onMouseEnter={onEnter} onMouseLeave={onLeave}>{reqSummary(group.req)}</span>
      <span className={`${cell} text-gray-600`} onClick={onClick} onMouseEnter={onEnter} onMouseLeave={onLeave}>{group.res ? "\u2192" : ""}</span>
      <span className={`${cell} -mr-1 rounded-r text-gray-400`} onClick={onClick} onMouseEnter={onEnter} onMouseLeave={onLeave}>{group.res ? resSummary(group.res) : ""}</span>
    </>
  );
}

function GridTextEntry({ line }: { line: LogLine }) {
  const [time, content] = lineTimeParts(line);
  return (
    <>
      <span className="text-gray-500 px-1 -ml-1">{time}</span>
      <span className="col-span-3 whitespace-pre-wrap break-all px-1">{content}</span>
    </>
  );
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
  const [hoveredLineId, setHoveredLineId] = useState<string | null>(null);
  const [hoveredRequestId, setHoveredRequestId] = useState<string | null>(null);
  const [groupRequests, setGroupRequests] = useState(() => localStorage.getItem("pref:groupRequests") === "true");

  const isProxy = source === "proxy";

  const filtered = useMemo(
    () => (isProxy ? lines : filterLines(lines, tab, showApiCalls)),
    [lines, tab, showApiCalls, isProxy],
  );

  const orderedRequestIds = useMemo(() => {
    const seen = new Set<string>();
    const ids: string[] = [];
    for (const line of lines) {
      if (line.request_id && !seen.has(line.request_id)) {
        seen.add(line.request_id);
        ids.push(line.request_id);
      }
    }
    return ids;
  }, [lines]);

  useEffect(() => {
    localStorage.setItem("pref:groupRequests", String(groupRequests));
  }, [groupRequests]);

  const groupedItems = useMemo((): DisplayItem[] => {
    if (!isProxy || !groupRequests) {
      return filtered.map((line) => ({ kind: "line" as const, line }));
    }
    const items: DisplayItem[] = [];
    const groupMap = new Map<string, RequestGroup>();
    for (const line of filtered) {
      if (!line.request_id) {
        items.push({ kind: "line", line });
        continue;
      }
      const rid = line.request_id;
      if (line.data.type === "request") {
        const group: RequestGroup = { kind: "group", request_id: rid, req: line.data, startTime: line.time, res: null, endTime: null };
        groupMap.set(rid, group);
        items.push(group);
      } else if (line.data.type === "response") {
        const group = groupMap.get(rid);
        if (group) { group.res = line.data; group.endTime = line.time; }
      } else {
        items.push({ kind: "line", line });
      }
    }
    return items;
  }, [filtered, isProxy, groupRequests]);

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
            {isProxy && (
              <ToggleSelector
                label="Group requests"
                checked={groupRequests}
                onChange={setGroupRequests}
              />
            )}
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
          {(isProxy && groupRequests) ? (
            <div className="grid" style={{ gridTemplateColumns: "max-content max-content max-content max-content" }}>
              {groupedItems.map((item) => item.kind === "group"
                ? <GridGroupEntry
                    key={item.request_id}
                    group={item}
                    isHovered={item.request_id === hoveredRequestId}
                    onClick={() => handleLineClick(item.request_id)}
                    onEnter={() => setHoveredRequestId(item.request_id)}
                    onLeave={() => setHoveredRequestId(null)}
                  />
                : <GridTextEntry key={item.line.id} line={item.line} />
              )}
            </div>
          ) : (
            groupedItems.map((item) => item.kind === "line"
              ? <FlatEntry
                  key={item.line.id}
                  line={item.line}
                  hoveredLineId={hoveredLineId}
                  hoveredRequestId={hoveredRequestId}
                  onClick={handleLineClick}
                  onEnter={(lid, rid) => { setHoveredLineId(lid); setHoveredRequestId(rid); }}
                  onLeave={() => { setHoveredLineId(null); setHoveredRequestId(null); }}
                />
              : null
            )
          )}
          <div ref={bottomRef} />
        </div>

        {/* request detail overlay */}
        {selectedEntry && (
          <RequestDetail entry={selectedEntry} onClose={() => setSelectedEntry(null)} requestIds={orderedRequestIds} />
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
