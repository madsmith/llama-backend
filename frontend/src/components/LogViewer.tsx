import { useEffect, useRef } from "react";

interface Props {
  lines: { id: number; text: string }[];
  connected: boolean;
  onClear: () => void;
}

export default function LogViewer({ lines, connected, onClear }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines.length]);

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-gray-500">
          {connected ? "Connected" : "Disconnected"} &middot; {lines.length}{" "}
          lines
        </span>
        <button
          onClick={onClear}
          className="text-xs text-gray-500 hover:text-gray-300 transition"
        >
          Clear
        </button>
      </div>
      <div className="flex-1 overflow-auto rounded-lg bg-black p-3 font-mono text-xs leading-5 text-gray-300">
        {lines.length === 0 && (
          <span className="text-gray-600">No log output yet.</span>
        )}
        {lines.map((l) => (
          <div key={l.id} className="whitespace-pre-wrap break-all">
            {l.text}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
