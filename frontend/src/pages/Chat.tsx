import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import {
  type ChatSession,
  type Msg,
  chatLabel,
  errorText,
  formatStats,
  loadActiveId,
  loadChats,
  newChat,
  parseChunk,
  saveActiveId,
  saveChats,
  summarizeChat,
  toApiMessages,
} from "../api/chat";

const MODEL_PREF = "pref:chatModel";
const SYSTEM_PREF = "pref:chatSystemPrompt";
const DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant.";
const SAVE_DEBOUNCE_MS = 300;

function readPref(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}
function writePref(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* ignore */ }
}

const byRecent = (a: ChatSession, b: ChatSession) => b.updatedAt - a.updatedAt;
const hasReply = (c: ChatSession) => c.messages.some((m) => m.role === "assistant" && !m.error && m.content);

function UserBubble({ msg }: { msg: Msg }) {
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="max-w-[75%] rounded-2xl rounded-tr-sm border border-blue-800/50 bg-blue-950/50 px-4 py-3">
        <div className="text-sm text-gray-200 leading-6 whitespace-pre-wrap break-words">{msg.content}</div>
      </div>
    </div>
  );
}

function AssistantBubble({ msg, pending }: { msg: Msg; pending: boolean }) {
  const [reasoningOpen, setReasoningOpen] = useState(false);
  const empty = !msg.content && !msg.reasoning && !msg.error;
  const stats = msg.stats ? formatStats(msg.stats, pending) : null;

  return (
    <div className="max-w-[85%] space-y-1">
      <div
        className={`rounded-2xl rounded-tl-sm border px-4 py-3 ${
          msg.error ? "border-red-900/60 bg-red-950/30" : "border-violet-900/60 bg-violet-950/35"
        }`}
      >
        {msg.reasoning && (
          <div className={msg.content || msg.error ? "mb-2.5" : undefined}>
            <button
              className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-300 transition-colors"
              onClick={() => setReasoningOpen(!reasoningOpen)}
            >
              <span className={`text-[10px] inline-block transition-transform ${reasoningOpen ? "rotate-90" : ""}`}>&#9654;</span>
              <span className="italic">{pending && !msg.content ? "reasoning…" : "reasoning"}</span>
            </button>
            {reasoningOpen && (
              <div className="mt-1.5 pl-3 border-l border-gray-700 text-xs text-gray-400 leading-5 whitespace-pre-wrap break-words">
                {msg.reasoning}
              </div>
            )}
          </div>
        )}
        {msg.content && (
          <div className="text-sm text-gray-200 leading-6 whitespace-pre-wrap break-words">{msg.content}</div>
        )}
        {msg.error && (
          <div className="text-xs text-red-300 font-mono whitespace-pre-wrap break-all">{msg.error}</div>
        )}
        {empty && pending && <span className="text-sm text-gray-500 animate-pulse">…</span>}
      </div>
      {stats && <div className="pl-1 text-[11px] text-gray-600 font-mono">{stats}</div>}
    </div>
  );
}

function ModelDivider({ model }: { model: string }) {
  return (
    <div className="flex items-center gap-2 py-1">
      <div className="flex-1 h-px bg-gray-800" />
      <span className="text-[11px] text-gray-500">
        model changed to <span className="font-mono text-gray-400">{model}</span>
      </span>
      <div className="flex-1 h-px bg-gray-800" />
    </div>
  );
}

function ChatList({ chats, activeId, streaming, onSelect, onDelete, onNew }: {
  chats: ChatSession[];
  activeId: number;
  streaming: Set<number>;
  onSelect: (id: number) => void;
  onDelete: (id: number) => void;
  onNew: () => void;
}) {
  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="pb-3 mb-2 border-b border-gray-800">
        <button
          onClick={onNew}
          className="w-full rounded-md border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-200 hover:bg-gray-700 hover:text-white transition text-left"
        >
          + New chat
        </button>
      </div>
      <ul className="flex-1 min-h-0 overflow-auto">
        {[...chats].sort(byRecent).map((c) => (
          <li key={c.id} className="group relative">
            <button
              onClick={() => onSelect(c.id)}
              title={chatLabel(c)}
              className={`w-full rounded-r-md border-l-2 pl-3 pr-7 py-1.5 text-left text-sm truncate ${
                c.id === activeId
                  ? "border-blue-500 bg-gray-800 text-white"
                  : "border-transparent text-gray-400 group-hover:bg-gray-800/60 group-hover:text-gray-200"
              }`}
            >
              {streaming.has(c.id) && <span className="mr-1.5 text-blue-400 animate-pulse">•</span>}
              {chatLabel(c)}
            </button>
            <button
              onClick={() => onDelete(c.id)}
              aria-label={`Delete ${chatLabel(c)}`}
              className="absolute right-1 top-1/2 -translate-y-1/2 p-1.5 text-gray-500 hover:text-red-400 transition-colors [@media(hover:hover)]:invisible [@media(hover:hover)]:group-hover:visible group-focus-within:visible"
            >
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 14 14" className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                <line x1="2" y1="2" x2="12" y2="12" />
                <line x1="12" y1="2" x2="2" y2="12" />
              </svg>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function initialState(): { chats: ChatSession[]; activeId: number } {
  let chats = loadChats();
  if (chats.length === 0) chats = [newChat([], readPref(MODEL_PREF) ?? "")];
  const saved = loadActiveId();
  const activeId = chats.some((c) => c.id === saved) ? saved! : [...chats].sort(byRecent)[0].id;
  return { chats, activeId };
}

export default function Chat() {
  const [init] = useState(initialState);
  const [chats, setChats] = useState<ChatSession[]>(init.chats);
  const [activeId, setActiveId] = useState<number>(init.activeId);
  const [models, setModels] = useState<string[]>([]);
  const [modelsError, setModelsError] = useState<string | null>(null);
  // A saved value (including "" after clearing) wins over the default.
  const [systemPrompt, setSystemPrompt] = useState(() => readPref(SYSTEM_PREF) ?? DEFAULT_SYSTEM_PROMPT);
  const [showSystem, setShowSystem] = useState(false);
  const [listOpen, setListOpen] = useState(false);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState<Set<number>>(new Set());

  const chatsRef = useRef(chats);
  chatsRef.current = chats;
  const aborts = useRef(new Map<number, AbortController>());
  const titling = useRef(new Set<number>());
  const bottomRef = useRef<HTMLDivElement>(null);

  const active = chats.find((c) => c.id === activeId) ?? chats[0];
  const busy = streaming.has(active.id);

  // ----- persistence -----
  useEffect(() => {
    const t = setTimeout(() => saveChats(chats), SAVE_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [chats]);
  useEffect(() => {
    const flush = () => saveChats(chatsRef.current);
    window.addEventListener("beforeunload", flush);
    return () => { window.removeEventListener("beforeunload", flush); flush(); };
  }, []);
  useEffect(() => { saveActiveId(active.id); }, [active.id]);
  useEffect(() => { writePref(SYSTEM_PREF, systemPrompt); }, [systemPrompt]);
  useEffect(() => {
    const ctrls = aborts.current;
    return () => ctrls.forEach((c) => c.abort());
  }, []);

  const updateChat = useCallback((id: number, fn: (c: ChatSession) => ChatSession) => {
    setChats((prev) => prev.map((c) => (c.id === id ? fn(c) : c)));
  }, []);

  const updateLastMsg = (id: number, fn: (m: Msg) => Msg) =>
    updateChat(id, (c) => ({ ...c, messages: [...c.messages.slice(0, -1), fn(c.messages[c.messages.length - 1])] }));

  // ----- models -----
  const loadModels = useCallback(() => {
    api.getChatModels()
      .then((ids) => { setModels(ids); setModelsError(null); })
      .catch((e: Error) => setModelsError(e.message));
  }, []);
  useEffect(() => { loadModels(); }, [loadModels]);

  // Give a chat with no model yet the last-used one (or the first available).
  useEffect(() => {
    if (active.model || models.length === 0) return;
    const pref = readPref(MODEL_PREF);
    const model = pref && models.includes(pref) ? pref : models[0];
    updateChat(active.id, (c) => ({ ...c, model }));
  }, [active.id, active.model, models, updateChat]);

  const setModel = (model: string) => {
    updateChat(active.id, (c) => ({ ...c, model }));
    writePref(MODEL_PREF, model);
  };

  // ----- titles -----
  const generateTitle = useCallback(async (chat: ChatSession) => {
    if (titling.current.has(chat.id) || !chat.model || !hasReply(chat)) return;
    titling.current.add(chat.id);
    try {
      const title = await summarizeChat(chat);
      if (title) updateChat(chat.id, (c) => ({ ...c, title, titledAt: chat.messages.length }));
    } finally {
      titling.current.delete(chat.id);
    }
  }, [updateChat]);

  // ----- navigation -----
  const selectChat = (id: number) => {
    setListOpen(false);
    if (id === active.id) return;
    // Refresh the blurb of the chat being left if it grew since it was titled,
    // and retry the one being opened if it never got a title.
    if (!streaming.has(active.id) && active.messages.length > active.titledAt) generateTitle(active);
    const next = chats.find((c) => c.id === id);
    if (next && next.title === null && !streaming.has(id)) generateTitle(next);
    setActiveId(id);
  };

  const startNewChat = () => {
    setListOpen(false);
    if (active.messages.length === 0) return; // already on a blank chat
    if (!busy && active.messages.length > active.titledAt) generateTitle(active);
    const chat = newChat(chats, active.model);
    setChats((prev) => [...prev, chat]);
    setActiveId(chat.id);
  };

  const deleteChat = (id: number) => {
    aborts.current.get(id)?.abort();
    const remaining = chats.filter((c) => c.id !== id);
    if (remaining.length === 0) {
      const fresh = newChat(chats, active.model);
      setChats([fresh]);
      setActiveId(fresh.id);
      return;
    }
    setChats(remaining);
    if (id === active.id) setActiveId([...remaining].sort(byRecent)[0].id);
  };

  // ----- sending -----
  useEffect(() => { bottomRef.current?.scrollIntoView({ block: "end" }); }, [active.id, active.messages]);

  const send = async () => {
    const text = input.trim();
    const chat = active;
    if (!text || !chat.model || streaming.has(chat.id)) return;

    const userMsg: Msg = { role: "user", content: text, model: chat.model };
    const history = [...chat.messages, userMsg];
    const tSend = performance.now();
    updateChat(chat.id, (c) => ({
      ...c,
      updatedAt: Date.now(),
      messages: [...history, { role: "assistant", content: "", model: chat.model, stats: { tSend, chunks: 0 } }],
    }));
    setInput("");

    const controller = new AbortController();
    aborts.current.set(chat.id, controller);
    setStreaming((s) => new Set(s).add(chat.id));

    let finalContent = "";
    let failed = false;

    try {
      const res = await fetch("/api/chat/completions", {
        method: "POST",
        headers: { "content-type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          model: chat.model,
          stream: true,
          stream_options: { include_usage: true },
          messages: toApiMessages(history, systemPrompt),
        }),
      });

      if (!res.ok || !res.body) {
        const errBody = await res.text().catch(() => "");
        let msg = `${res.status} ${res.statusText}`;
        try { msg = errorText(JSON.parse(errBody).error ?? msg); } catch { if (errBody) msg = errBody; }
        failed = true;
        updateLastMsg(chat.id, (m) => ({ ...m, error: msg }));
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          const d = parseChunk(line.slice(5).trim());
          if (!d) continue;
          if (d.error) failed = true;
          finalContent += d.content ?? "";
          const now = performance.now();
          const hasText = !!(d.content || d.reasoning);
          updateLastMsg(chat.id, (m) => {
            const st = m.stats!;
            return {
              ...m,
              content: m.content + (d.content ?? ""),
              reasoning: d.reasoning ? (m.reasoning ?? "") + d.reasoning : m.reasoning,
              error: d.error ?? m.error,
              stats: {
                ...st,
                tFirst: hasText ? st.tFirst ?? now : st.tFirst,
                tLast: hasText ? now : st.tLast,
                chunks: st.chunks + (hasText ? 1 : 0),
                timings: d.timings ?? st.timings,
                usage: d.usage ?? st.usage,
              },
            };
          });
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        failed = true;
        updateLastMsg(chat.id, (m) => ({ ...m, error: (e as Error).message }));
      }
    } finally {
      aborts.current.delete(chat.id);
      setStreaming((s) => { const n = new Set(s); n.delete(chat.id); return n; });
    }

    // Title the chat after its first successful reply.
    const stillExists = chatsRef.current.some((c) => c.id === chat.id);
    if (!failed && finalContent && chat.title === null && stillExists) {
      generateTitle({
        ...chat,
        messages: [...history, { role: "assistant", content: finalContent, model: chat.model }],
      });
    }
  };

  const stop = () => aborts.current.get(active.id)?.abort();

  // The select shows the chat's model even if the proxy no longer lists it.
  const modelOptions = active.model && !models.includes(active.model) ? [active.model, ...models] : models;

  return (
    <div className="relative flex h-full min-h-0 gap-4">
      {/* chat list */}
      <aside
        className={`${listOpen ? "flex" : "hidden"} md:flex flex-col w-56 shrink-0 md:static absolute inset-y-0 left-0 z-20 bg-gray-950 md:bg-transparent pr-3 md:border-r border-gray-800`}
      >
        <ChatList
          chats={chats}
          activeId={active.id}
          streaming={streaming}
          onSelect={selectChat}
          onDelete={deleteChat}
          onNew={startNewChat}
        />
      </aside>
      {listOpen && <div className="md:hidden absolute inset-0 z-10 bg-black/40" onClick={() => setListOpen(false)} />}

      {/* active chat */}
      <div className="flex flex-col flex-1 min-w-0 min-h-0 max-w-4xl mx-auto">
        <div className="flex flex-wrap items-center gap-3 pb-4 border-b border-gray-800">
          <button
            onClick={() => setListOpen(true)}
            className="md:hidden rounded-md px-3 py-2 text-sm text-gray-400 hover:bg-gray-800 hover:text-white transition"
          >
            Chats
          </button>
          <h1 className="text-xl font-semibold mr-auto truncate min-w-0" title={chatLabel(active)}>
            {chatLabel(active)}
          </h1>
          <select
            value={active.model}
            onChange={(e) => setModel(e.target.value)}
            disabled={modelOptions.length === 0}
            className="rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 font-mono focus:border-blue-500 focus:outline-none max-w-xs"
          >
            {modelOptions.length === 0 && <option value="">No models</option>}
            {modelOptions.map((id) => <option key={id} value={id}>{id}</option>)}
          </select>
          <button
            onClick={loadModels}
            className="rounded-md px-3 py-2 text-sm text-gray-400 hover:bg-gray-800 hover:text-white transition"
            title="Reload model list"
          >
            Refresh
          </button>
          <button
            onClick={() => setShowSystem((v) => !v)}
            className={`rounded-md px-3 py-2 text-sm transition ${
              showSystem || systemPrompt.trim() ? "bg-gray-800 text-white" : "text-gray-400 hover:bg-gray-800 hover:text-white"
            }`}
          >
            System prompt
          </button>
        </div>

        {modelsError && (
          <div className="mt-3 rounded-md border border-red-900/60 bg-red-950/30 px-3 py-2 text-sm text-red-300">
            Could not load models: {modelsError}
          </div>
        )}

        {showSystem && (
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="Optional system prompt"
            rows={3}
            className="mt-3 w-full rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none resize-y"
          />
        )}

        {/* transcript */}
        <div className="flex-1 min-h-0 overflow-auto py-4 space-y-3">
          {active.messages.length === 0 && (
            <div className="h-full flex items-center justify-center text-sm text-gray-600">
              {active.model ? `Send a message to ${active.model}` : "Select a model to start"}
            </div>
          )}
          {active.messages.map((m, i) => {
            const prevModel = active.messages.slice(0, i).reverse().find((p) => p.model)?.model;
            const changed = !!m.model && !!prevModel && m.model !== prevModel;
            return (
              <div key={i} className="space-y-3">
                {changed && <ModelDivider model={m.model!} />}
                {m.role === "user" ? (
                  <UserBubble msg={m} />
                ) : (
                  <AssistantBubble msg={m} pending={busy && i === active.messages.length - 1} />
                )}
              </div>
            );
          })}
          <div ref={bottomRef} />
        </div>

        {/* composer */}
        <div className="flex gap-2 pt-3 border-t border-gray-800">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="Message (Enter to send, Shift+Enter for newline)"
            rows={2}
            className="flex-1 rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-100 focus:border-blue-500 focus:outline-none resize-none"
          />
          {busy ? (
            <button
              onClick={stop}
              className="rounded-md bg-red-900/60 px-4 text-sm font-medium text-red-100 hover:bg-red-800/70 transition"
            >
              Stop
            </button>
          ) : (
            <button
              onClick={send}
              disabled={!input.trim() || !active.model}
              className="rounded-md bg-blue-600 px-4 text-sm font-medium text-white hover:bg-blue-500 transition disabled:opacity-40 disabled:hover:bg-blue-600"
            >
              Send
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
