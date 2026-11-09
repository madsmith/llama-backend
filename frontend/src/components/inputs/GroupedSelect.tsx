import { type ReactNode, useEffect, useRef, useState } from "react";

export interface SelectOption {
  key: string;
  /** Main line, rendered in monospace. */
  label: string;
  /** Optional dim second line. */
  detail?: string;
}

export interface SelectSection {
  key: string;
  /** Section header; omit for a flat list. */
  header?: ReactNode;
  options: SelectOption[];
  /** Shown when the section has no options. */
  empty?: string;
}

/**
 * Custom dropdown listbox with optional section headers. Arrow keys move
 * between options, Escape closes, clicking outside closes.
 */
export default function GroupedSelect({ sections, selected, trigger, sizeTo, onSelect, disabled, ariaLabel }: {
  sections: SelectSection[];
  selected: string | null | undefined;
  /** Content of the closed control. */
  trigger: ReactNode;
  /**
   * Every value the trigger can show. The control reserves the widest one so
   * its width doesn't change with the selection.
   */
  sizeTo?: ReactNode[];
  onSelect: (key: string) => void;
  disabled?: boolean;
  ariaLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    // Focus the selected option (or the first) so arrow keys work immediately.
    const sel = listRef.current?.querySelector<HTMLButtonElement>("[aria-selected=true]");
    const first = listRef.current?.querySelector<HTMLButtonElement>("[role=option]");
    (sel ?? first)?.focus();
    sel?.scrollIntoView({ block: "nearest" });
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const onListKey = (e: React.KeyboardEvent) => {
    const opts = Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>("[role=option]") ?? []);
    const i = opts.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = e.key === "ArrowDown" ? Math.min(i + 1, opts.length - 1) : Math.max(i - 1, 0);
      opts[next]?.focus();
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      buttonRef.current?.focus();
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  };

  const grouped = sections.some((s) => s.header !== undefined);

  return (
    <div ref={rootRef} className="relative min-w-0 max-w-full">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-left text-sm text-gray-100 focus:border-blue-500 focus:outline-none disabled:opacity-50"
      >
        {/* All candidates share one grid cell: the widest sets the width. */}
        <span className="grid min-w-0 flex-1">
          {sizeTo?.map((n, i) => (
            <span key={i} aria-hidden className="invisible col-start-1 row-start-1 overflow-hidden whitespace-nowrap">{n}</span>
          ))}
          <span className="col-start-1 row-start-1 truncate">{trigger}</span>
        </span>
        <span className="text-[10px] text-gray-500">&#9662;</span>
      </button>

      {open && (
        <div
          ref={listRef}
          role="listbox"
          aria-label={ariaLabel}
          onKeyDown={onListKey}
          className="absolute left-0 z-30 mt-1 max-h-96 w-max min-w-full max-w-[min(36rem,calc(100vw-2rem))] overflow-auto rounded-md border border-gray-700 bg-gray-900 py-1 shadow-xl shadow-black/40"
        >
          {sections.map((sec) => (
            <div key={sec.key} role={grouped ? "group" : undefined} className={grouped ? "pb-1" : undefined}>
              {sec.header !== undefined && (
                <div className="flex items-baseline gap-2 px-3 pt-2 pb-1 text-[11px] uppercase tracking-wide text-gray-500">
                  {sec.header}
                </div>
              )}
              {sec.options.length === 0 && sec.empty && (
                <div className={`${grouped ? "pl-5" : "pl-3"} pr-3 py-1 text-xs text-gray-600`}>{sec.empty}</div>
              )}
              {sec.options.map((o) => {
                const isSel = o.key === selected;
                return (
                  <button
                    key={o.key}
                    type="button"
                    role="option"
                    aria-selected={isSel}
                    onClick={() => { onSelect(o.key); setOpen(false); buttonRef.current?.focus(); }}
                    className={`block w-full border-l-2 py-1.5 ${grouped ? "pl-5" : "pl-3"} pr-3 text-left focus:outline-none ${
                      isSel
                        ? "border-blue-500 bg-gray-800"
                        : "border-transparent hover:bg-gray-800/60 focus:bg-gray-800/60"
                    }`}
                  >
                    <div className={`truncate font-mono text-sm ${isSel ? "text-white" : "text-gray-200"}`}>{o.label}</div>
                    {o.detail && <div className="truncate text-[11px] text-gray-500">{o.detail}</div>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
