import { useEffect, useRef, useState, useMemo } from "react";
import { TabButton, PANEL_BG } from "./TabBar";

type LogLine = { id: number; text: string };

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
}

export default function LogViewer({ lines, connected, onClear }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const [tab, setTab] = useState<Tab>("server");
  const [showApiCalls, setShowApiCalls] = useState(false);

  const filtered = useMemo(
    () => filterLines(lines, tab, showApiCalls),
    [lines, tab, showApiCalls],
  );

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [filtered.length]);

  return (
    <div className="flex flex-col h-full">
      {/* tabs */}
      <div className="flex items-end gap-1.5">
        <TabButton label="Server" active={tab === "server"} onClick={() => setTab("server")} />
        <TabButton label="Info" active={tab === "info"} onClick={() => setTab("info")} />
        <TabButton label="Other" active={tab === "other"} onClick={() => setTab("other")} />
        <div className="flex-1" />
        <TabButton label="All" active={tab === "all"} onClick={() => setTab("all")} />
      </div>

      {/* panel */}
      <div className={`flex flex-col flex-1 min-h-0 ${PANEL_BG} rounded-b-lg`}>
        {/* toolbar area */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
          <div className="flex items-center gap-4">
            {tab === "server" && (
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
        <div className="flex-1 overflow-auto p-4 font-mono text-xs leading-5 text-gray-300">
          {filtered.length === 0 && (
            <span className="text-gray-600">No log output yet.</span>
          )}
          {filtered.map((l) => (
            <div key={l.id} className="whitespace-pre-wrap break-all">
              {l.text}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  );
}
