import { useState } from "react";

interface Props {
  text: string;
  limit?: number;
  className?: string;
}

export default function LongString({ text, limit = 80, className = "" }: Props) {
  const [expanded, setExpanded] = useState(false);

  if (text.length <= limit) {
    return <span className={`whitespace-pre-wrap break-all ${className}`}>{text}</span>;
  }

  const prefixLen = Math.floor(limit / 2);
  const suffixLen = limit - prefixLen;

  return (
    <span
      onClick={() => { if (!window.getSelection()?.toString()) setExpanded(!expanded); }}
      className={`cursor-pointer ${className}`}
    >
      {expanded ? (
        <span className="whitespace-pre-wrap break-all">{text}</span>
      ) : (
        <>
          <span className="whitespace-pre-wrap">{text.slice(0, prefixLen)}</span>
          <span className="text-rose-400/60">…({text.length - prefixLen - suffixLen})…</span>
          <span className="whitespace-pre-wrap">{text.slice(-suffixLen)}</span>
        </>
      )}
    </span>
  );
}
