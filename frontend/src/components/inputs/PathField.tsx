import { useState } from "react";
import { Tip } from "./Tip";
import FileBrowser from "../FileBrowser";

interface Props {
  label: string;
  tip?: React.ReactNode;
  note?: React.ReactNode;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  bg?: "gray-800" | "gray-900";
}

export default function PathField({
  label,
  tip,
  note,
  value,
  onChange,
  placeholder,
  bg = "gray-900",
}: Props) {
  const [showBrowser, setShowBrowser] = useState(false);

  return (
    <div>
      <label className="block text-sm font-medium text-gray-400 mb-1">
        {label}
        {tip && <Tip>{tip}</Tip>}
      </label>
      <div className="relative">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          spellCheck={false}
          className={[
            "w-full rounded-md border border-gray-700 pl-3 pr-9 py-2 text-sm text-gray-100 font-mono focus:border-blue-500 focus:outline-none",
            bg === "gray-800" ? "bg-gray-800" : "bg-gray-900",
          ].join(" ")}
        />
        <button
          type="button"
          onClick={() => setShowBrowser(true)}
          title="Browse filesystem"
          className="absolute right-1 inset-y-1 px-2 flex items-center text-gray-600 hover:text-gray-300 transition rounded hover:bg-gray-700"
          aria-label="Browse filesystem"
        >
          <BrowseIcon />
        </button>
      </div>
      {note && <p className="mt-1 text-xs text-gray-600">{note}</p>}
      {showBrowser && (
        <FileBrowser
          initialPath={value}
          onConfirm={(path) => {
            onChange(path);
            setShowBrowser(false);
          }}
          onClose={() => setShowBrowser(false)}
        />
      )}
    </div>
  );
}

function BrowseIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      aria-hidden="true"
    >
      {/* Open folder */}
      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
    </svg>
  );
}
