import { useState } from "react";

function isCollapsible(value: unknown): value is Record<string, unknown> | unknown[] {
  return value !== null && typeof value === "object";
}

function preview(value: Record<string, unknown> | unknown[]): string {
  if (Array.isArray(value)) {
    return `Array(${value.length})`;
  }
  const keys = Object.keys(value);
  if (keys.length <= 3) return `{ ${keys.join(", ")} }`;
  return `{ ${keys.slice(0, 3).join(", ")}, ... }`;
}

function Chevron({ open }: { open: boolean }) {
  return (
    <span
      className={`inline-block w-3.5 text-gray-500 transition-transform select-none ${open ? "rotate-90" : ""}`}
    >
      &#9654;
    </span>
  );
}

const TRUNCATE_AT = 80;

function LongString({ value }: { value: string }) {
  const [expanded, setExpanded] = useState(false);

  if (value.length <= TRUNCATE_AT) {
    return <span className="text-green-400">"{value}"</span>;
  }

  const prefixLen = Math.floor(TRUNCATE_AT / 2);
  const suffixLen = TRUNCATE_AT - prefixLen;

  return (
    <span
      onClick={() => setExpanded(!expanded)}
      className="text-green-400 cursor-pointer hover:bg-gray-800/50 rounded"
    >
      {expanded ? (
        <>"{value}"</>
      ) : (
        <>"{value.slice(0, prefixLen)}<span className="text-gray-500">...({value.length - prefixLen - suffixLen})</span>{value.slice(-suffixLen)}"</>
      )}
    </span>
  );
}

function ValueSpan({ value }: { value: unknown }) {
  if (value === null) return <span className="text-gray-500">null</span>;
  if (typeof value === "boolean")
    return <span className="text-yellow-400">{String(value)}</span>;
  if (typeof value === "number")
    return <span className="text-blue-400">{String(value)}</span>;
  if (typeof value === "string")
    return <LongString value={value} />;
  return <span className="text-gray-300">{String(value)}</span>;
}

function JsonNode({
  name,
  value,
  defaultOpen = false,
  depth = 0,
  expandDepth = 0,
}: {
  name?: string;
  value: unknown;
  defaultOpen?: boolean;
  depth?: number;
  expandDepth?: number;
}) {
  const [open, setOpen] = useState(defaultOpen || depth < expandDepth);

  if (!isCollapsible(value)) {
    return (
      <div className="flex gap-1 py-px pl-5">
        {name != null && <span className="text-gray-400">{name}:</span>}
        <ValueSpan value={value} />
      </div>
    );
  }

  const entries = Array.isArray(value)
    ? value.map((v, i) => [String(i), v] as const)
    : Object.entries(value);

  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 py-px hover:bg-gray-800/50 rounded w-full text-left"
      >
        <Chevron open={open} />
        {name != null && <span className="text-gray-400">{name}:</span>}
        {!open && (
          <span className="text-gray-600 text-xs ml-1">{preview(value)}</span>
        )}
      </button>
      {open && (
        <div className="pl-4 border-l border-gray-800 ml-1.5">
          {entries.map(([k, v]) => (
            <JsonNode key={k} name={k} value={v} depth={depth + 1} expandDepth={expandDepth} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function JsonTree({
  data,
  defaultOpen = true,
  defaultExpandDepth = 1,
}: {
  data: unknown;
  defaultOpen?: boolean;
  defaultExpandDepth?: number;
}) {
  return (
    <div className="font-mono text-xs leading-5">
      <JsonNode value={data} defaultOpen={defaultOpen} expandDepth={defaultExpandDepth} />
    </div>
  );
}
