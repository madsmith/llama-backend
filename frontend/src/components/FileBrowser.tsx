import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";

interface Entry {
  name: string;
  type: "dir" | "file";
}

interface Props {
  initialPath: string;
  onConfirm: (path: string) => void;
  onClose: () => void;
}

const GGUF_EXT = ".gguf";

function joinPath(dir: string, name: string): string {
  return dir.endsWith("/") ? dir + name : dir + "/" + name;
}

export default function FileBrowser({ initialPath, onConfirm, onClose }: Props) {
  const [dirPath, setDirPath] = useState("");
  const [inputText, setInputText] = useState(initialPath);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(
    initialPath || null,
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const loadDir = useCallback(async (path: string, preSelect?: string) => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.browseFs(path);
      setDirPath(result.path);
      setInputText(result.path);
      setEntries(result.entries);
      if (preSelect) {
        const match = result.entries.find(
          (e) => e.type === "file" && e.name === preSelect,
        );
        if (match) {
          setSelectedPath(joinPath(result.path, preSelect));
        }
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load — if given a file path, browse its parent and pre-select the file
  useEffect(() => {
    const lastSlash = initialPath.lastIndexOf("/");
    const tail = lastSlash >= 0 ? initialPath.slice(lastSlash + 1) : initialPath;
    const looksLikeFile = tail.includes(".");
    loadDir(initialPath, looksLikeFile ? tail : undefined);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const navigate = (name: string) => {
    setSelectedPath(null);
    loadDir(joinPath(dirPath, name));
  };

  const navigateUp = () => {
    setSelectedPath(null);
    loadDir(dirPath + "/..");
  };

  const handleInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      setSelectedPath(null);
      loadDir(inputText);
    }
  };

  const visibleEntries = entries.filter((e) => {
    if (e.type === "dir") return true;
    if (showAll) return true;
    return e.name.toLowerCase().endsWith(GGUF_EXT);
  });

  // Root on both Unix ("/") and Windows ("C:\", etc.)
  const atRoot = dirPath === "/" || /^[A-Za-z]:[/\\]?$/.test(dirPath);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
    >
      <div
        className="bg-gray-900 border border-gray-700 rounded-lg w-full max-w-2xl mx-4 flex flex-col max-h-[80vh] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700 flex-shrink-0">
          <h2 className="text-base font-semibold text-gray-100">
            Select Model File
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-500 hover:text-gray-200 transition leading-none p-1 -mr-1 rounded hover:bg-gray-800"
            aria-label="Close"
          >
            <CloseIcon />
          </button>
        </div>

        {/* Path input */}
        <div className="px-5 py-3 border-b border-gray-700 flex-shrink-0">
          <input
            type="text"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={handleInputKeyDown}
            className="w-full rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 font-mono focus:border-blue-500 focus:outline-none"
            placeholder="/path/to/model.gguf"
            spellCheck={false}
          />
        </div>

        {/* File list */}
        <div className="flex-1 overflow-y-auto min-h-0">
          {loading ? (
            <div className="px-5 py-10 text-center text-sm text-gray-500">
              Loading...
            </div>
          ) : error ? (
            <div className="px-5 py-10 text-center text-sm text-red-400">
              {error}
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-gray-900">
                <tr className="border-b border-gray-800">
                  <th className="px-5 py-2 text-left text-xs font-medium text-gray-600 w-10">
                    Type
                  </th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-gray-600">
                    Name
                  </th>
                </tr>
              </thead>
              <tbody>
                {!atRoot && (
                  <tr
                    className="cursor-pointer hover:bg-gray-800 transition-colors border-b border-gray-800/40"
                    onClick={navigateUp}
                  >
                    <td className="px-5 py-2 text-gray-500">
                      <UpIcon />
                    </td>
                    <td className="px-3 py-2 text-gray-400">..</td>
                  </tr>
                )}
                {visibleEntries.map((entry) => {
                  const fullPath = joinPath(dirPath, entry.name);
                  const isSelected = selectedPath === fullPath;
                  return (
                    <tr
                      key={entry.name}
                      className={[
                        "cursor-pointer transition-colors border-b border-gray-800/40",
                        isSelected
                          ? "bg-blue-900/40"
                          : "hover:bg-gray-800",
                      ].join(" ")}
                      onClick={() => {
                        if (entry.type === "dir") {
                          navigate(entry.name);
                        } else {
                          setSelectedPath(fullPath);
                        }
                      }}
                    >
                      <td className="px-5 py-2 text-gray-500">
                        {entry.type === "dir" ? (
                          <FolderIcon />
                        ) : (
                          <FileIcon />
                        )}
                      </td>
                      <td
                        className={[
                          "px-3 py-2",
                          entry.type === "dir"
                            ? "text-gray-200"
                            : isSelected
                              ? "text-blue-200"
                              : "text-gray-300",
                        ].join(" ")}
                      >
                        {entry.name}
                      </td>
                    </tr>
                  );
                })}
                {visibleEntries.length === 0 && !loading && (
                  <tr>
                    <td
                      colSpan={2}
                      className="px-5 py-8 text-center text-sm text-gray-600"
                    >
                      {showAll
                        ? "Empty directory"
                        : "No .gguf files here — check \"Show all files\""}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-4 border-t border-gray-700 flex-shrink-0">
          <label className="flex items-center gap-2 text-sm text-gray-400 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={showAll}
              onChange={(e) => setShowAll(e.target.checked)}
              className="rounded border-gray-600 accent-blue-600"
            />
            Show all files
          </label>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-gray-700 px-4 py-2 text-sm text-gray-400 hover:text-gray-200 hover:border-gray-600 transition"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => selectedPath && onConfirm(selectedPath)}
              disabled={!selectedPath}
              className="rounded-md bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-600 disabled:opacity-40 transition"
            >
              Ok
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function FolderIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M10 4H4c-1.11 0-2 .89-2 2v12c0 1.11.89 2 2 2h16c1.11 0 2-.89 2-2V8c0-1.11-.89-2-2-2h-8l-2-2z" />
    </svg>
  );
}

function FileIcon() {
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
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
    </svg>
  );
}

function UpIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.25"
      aria-hidden="true"
    >
      <path d="M12 19V5M5 12l7-7 7 7" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden="true"
    >
      <path d="M18 6L6 18M6 6l12 12" />
    </svg>
  );
}
