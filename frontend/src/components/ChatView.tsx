import { useState, useEffect } from "react";
import LongString from "./LongString";

interface ContentBlock {
  type: string;
  text?: string;
}

interface ToolCall {
  id: string;
  type: string;
  function: { name: string; arguments: string };
}

type MsgContent = string | ContentBlock[] | null;

interface ChatMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: MsgContent;
  reasoning_content?: string;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
}

function getText(content: MsgContent): string {
  if (!content) return "";
  if (typeof content === "string") return content;
  return content
    .filter((b) => b.type === "text" && b.text)
    .map((b) => b.text!)
    .join("\n");
}


function Chevron({ open }: { open: boolean }) {
  return (
    <span className={`text-[10px] text-gray-500 transition-transform select-none inline-block ${open ? "rotate-90" : ""}`}>
      &#9654;
    </span>
  );
}

// Narrow chip for a single tool call within an assistant message
function ToolCallChip({ tc }: { tc: ToolCall }) {
  const [open, setOpen] = useState(false);
  let args: unknown = tc.function.arguments;
  try { args = JSON.parse(tc.function.arguments); } catch { /* keep as string */ }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-800/70 border border-gray-700/50 text-xs hover:bg-gray-700/60 transition-colors"
      >
        <Chevron open={false} />
        <span className="text-gray-500">Tool:</span>
        <span className="text-amber-400 font-medium">{tc.function.name}</span>
      </button>
    );
  }

  return (
    <div className="rounded-lg border border-gray-700/50 bg-gray-800/70 text-xs overflow-hidden w-full max-w-2xl">
      <button
        className="flex items-center gap-2 px-3 py-1.5 w-full text-left hover:bg-gray-700/40 transition-colors"
        onClick={() => setOpen(false)}
      >
        <Chevron open={true} />
        <span className="text-amber-400 font-medium flex-1">{tc.function.name}</span>
        <span className="text-gray-600 font-mono text-[10px]">{tc.id}</span>
      </button>
      <div className="px-3 py-2 border-t border-gray-700/50 font-mono text-gray-300 whitespace-pre-wrap break-all leading-5">
        {typeof args === "object" ? JSON.stringify(args, null, 2) : String(args)}
      </div>
    </div>
  );
}

// Right-aligned user bubble (~75% width)
function UserBubble({ msg }: { msg: ChatMessage }) {
  const text = getText(msg.content);
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="w-3/4 rounded-2xl rounded-tr-sm border border-blue-800/50 bg-blue-950/50 px-4 py-3">
        <div className="text-xs text-gray-200 leading-5">
          {text ? <LongString text={text} limit={2000} /> : <span className="text-gray-500">(empty)</span>}
        </div>
      </div>
      <span className="text-sm text-gray-600 pr-1">User</span>
    </div>
  );
}

type ReasoningMode = "off" | "collapsed" | "expanded";

// Left-aligned assistant bubble; if only tool_calls, skip the bubble wrapper
function AssistantBubble({ msg, showTools = true, reasoningMode = "off" }: {
  msg: ChatMessage;
  showTools?: boolean;
  reasoningMode?: ReasoningMode;
}) {
  const [reasoningOpen, setReasoningOpen] = useState(reasoningMode === "expanded");
  useEffect(() => {
    setReasoningOpen(reasoningMode === "expanded");
  }, [reasoningMode]);

  const text = getText(msg.content);
  const hasContent = !!text;
  const hasToolCalls = Array.isArray(msg.tool_calls) && msg.tool_calls.length > 0;
  const hasOnlyReasoning = !hasContent && !!msg.reasoning_content && (!hasToolCalls || !showTools);

  const chips = hasToolCalls && showTools ? (
    <div className="space-y-1.5">
      {msg.tool_calls!.map((tc) => <ToolCallChip key={tc.id} tc={tc} />)}
    </div>
  ) : null;

  // No content, no visible tools — only reasoning
  if (hasOnlyReasoning && reasoningMode === "off") return null;

  if (!hasContent && !msg.reasoning_content) {
    return chips ? <div>{chips}</div> : null;
  }

  return (
    <div className="max-w-[85%] space-y-1.5">
      <div className="rounded-2xl rounded-tl-sm border border-violet-900/60 bg-violet-950/35 px-4 py-3">
        {msg.reasoning_content && reasoningMode !== "off" && (
          <div className="mb-2.5">
            <button
              className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-300 transition-colors"
              onClick={() => setReasoningOpen(!reasoningOpen)}
            >
              <Chevron open={reasoningOpen} />
              <span className="italic">reasoning</span>
            </button>
            {reasoningOpen && (
              <div className="mt-1.5 pl-3 border-l border-gray-700 text-xs text-gray-400 leading-5">
                <LongString text={msg.reasoning_content} limit={1000} />
              </div>
            )}
          </div>
        )}
        {text && (
          <div className="text-xs text-gray-200 leading-5">
            <LongString text={text} limit={1000} />
          </div>
        )}
      </div>
      {chips}
      <span className="text-sm text-gray-600 pl-1">Assistant</span>
    </div>
  );
}

// Narrow collapsed pill → full-width expanded block
function ToolResultBubble({ msg, allToolCalls }: { msg: ChatMessage; allToolCalls: ToolCall[] }) {
  const [open, setOpen] = useState(false);
  const text = getText(msg.content);
  const match = allToolCalls.find((tc) => tc.id === msg.tool_call_id);
  const fnName = match?.function.name ?? "tool";

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-800/50 border border-gray-700/40 text-xs hover:bg-gray-700/50 transition-colors"
      >
        <Chevron open={false} />
        <span className="text-gray-500">Tool Output:</span>
        <span className="text-amber-400 font-medium">{fnName}</span>
      </button>
    );
  }

  return (
    <div className="rounded-lg border border-amber-900/40 bg-amber-950/20 text-xs overflow-hidden w-full max-w-2xl">
      <button
        className="flex items-center gap-2 px-3 py-1.5 w-full text-left hover:bg-amber-900/10 transition-colors"
        onClick={() => setOpen(false)}
      >
        <Chevron open={true} />
        <span className="text-amber-400 font-medium flex-1">{fnName}</span>
        <span className="text-gray-600 font-mono text-[10px]">{msg.tool_call_id}</span>
      </button>
      <div className="px-3 py-2 border-t border-amber-900/30 text-gray-300 leading-5">
        {text ? <LongString text={text} limit={2000} /> : <span className="text-gray-600">(empty)</span>}
      </div>
    </div>
  );
}

function MetaBar({ model, messageCount, systemMessages, tools, showTools, onToggleTools, reasoningMode, onSetReasoning }: {
  model: string;
  messageCount: number;
  systemMessages: ChatMessage[];
  tools: Array<{ function?: { name?: string } }>;
  showTools: boolean;
  onToggleTools: () => void;
  reasoningMode: ReasoningMode;
  onSetReasoning: (mode: ReasoningMode) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative shrink-0 border-b border-gray-800 text-xs">
      <div className="flex items-center bg-gray-800/20">
        <button
          className="flex items-center gap-2 px-4 py-2 flex-1 text-left hover:bg-gray-800/40 transition-colors"
          onClick={() => setOpen(!open)}
        >
          <Chevron open={open} />
          <span className="text-gray-300 font-mono">{model}</span>
          <span className="text-gray-600">&middot;</span>
          <span className="text-gray-500">{messageCount} messages</span>
          {tools.length > 0 && (
            <>
              <span className="text-gray-600">&middot;</span>
              <span className="text-gray-500">{tools.length} tools</span>
            </>
          )}
        </button>
        <label
          className="flex items-center gap-2 mr-3 cursor-pointer"
          onClick={(e) => e.stopPropagation()}
        >
          <span className="text-xs text-gray-500">Show Tools</span>
          <button
            type="button"
            role="switch"
            aria-checked={showTools}
            onClick={onToggleTools}
            className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${showTools ? "bg-green-600" : "bg-gray-600"}`}
          >
            <span className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200 ease-in-out ${showTools ? "translate-x-4" : "translate-x-0"}`} />
          </button>
        </label>
        <div
          className="flex items-center gap-2 mr-3"
          onClick={(e) => e.stopPropagation()}
        >
          <span className="text-xs text-gray-500">Reasoning</span>
          <div className="flex items-center rounded-full bg-gray-800 p-0.5 gap-0.5">
            {(["off", "collapsed", "expanded"] as const).map((mode) => (
              <button
                key={mode}
                onClick={() => onSetReasoning(mode)}
                className={`px-2 py-0.5 rounded-full text-xs font-medium transition-colors capitalize ${reasoningMode === mode ? "bg-gray-600 text-gray-100" : "text-gray-500 hover:text-gray-300"}`}
              >
                {mode === "off" ? "Off" : mode === "collapsed" ? "Fold" : "Full"}
              </button>
            ))}
          </div>
        </div>
      </div>
      {open && (
        <>
        <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
        <div className="absolute left-24 right-24 top-full z-20 flex flex-col max-h-[55vh] bg-gray-950 border border-gray-600 rounded-b-lg shadow-2xl">
          {/* System prompt - scrollable content */}
          <div className="flex-1 overflow-auto min-h-0 p-4">
            {systemMessages.map((m, i) => (
              <div key={i}>
                <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500 mb-2">System Prompt</div>
                <div className="text-xs text-gray-400 leading-5">
                  <LongString text={getText(m.content)} limit={2000} />
                </div>
              </div>
            ))}
          </div>
          {/* Tools footer */}
          {tools.length > 0 && (
            <div className="shrink-0 border-t border-gray-700 px-4 py-2.5">
              <div className="flex flex-wrap gap-1">
                {tools.map((t, i) => (
                  <span key={i} className="bg-gray-800 text-gray-400 rounded px-1.5 py-0.5 font-mono">
                    {t?.function?.name ?? "unknown"}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
        </>
      )}
    </div>
  );
}

function extractResponseMessage(
  responseBody: unknown,
  responseStatus: number | null | undefined,
): ChatMessage | "error" | null {
  if (!responseBody) return null;
  if (responseStatus === 200 || responseStatus == null) {
    const b = responseBody as Record<string, unknown>;
    const choices = b.choices;
    if (Array.isArray(choices) && choices.length > 0) {
      const msg = (choices[0] as Record<string, unknown>).message;
      if (msg && typeof msg === "object") return msg as ChatMessage;
    }
  }
  return "error";
}

export default function ChatView({ body, responseBody, responseStatus }: {
  body: Record<string, unknown>;
  responseBody?: unknown;
  responseStatus?: number | null;
}) {
  const model = typeof body.model === "string" ? body.model : "";
  const rawMessages = Array.isArray(body.messages) ? (body.messages as ChatMessage[]) : [];
  const tools = Array.isArray(body.tools)
    ? (body.tools as Array<{ function?: { name?: string } }>)
    : [];

  const systemMessages = rawMessages.filter((m) => m.role === "system");
  const chatMessages = rawMessages.filter((m) => m.role !== "system");

  const allToolCalls: ToolCall[] = rawMessages
    .filter((m) => m.role === "assistant" && Array.isArray(m.tool_calls))
    .flatMap((m) => m.tool_calls as ToolCall[]);

  const responseMsg = extractResponseMessage(responseBody, responseStatus);
  const [showTools, setShowTools] = useState(false);
  const [reasoningMode, setReasoningMode] = useState<ReasoningMode>("off");

  return (
    <div className="flex flex-col h-full min-h-0">
      <MetaBar
        model={model}
        messageCount={chatMessages.length}
        systemMessages={systemMessages}
        tools={tools}
        showTools={showTools}
        onToggleTools={() => setShowTools((v) => !v)}
        reasoningMode={reasoningMode}
        onSetReasoning={setReasoningMode}
      />
      <div className="flex-1 overflow-auto px-4 py-4 space-y-2">
        {chatMessages.map((msg, i) => {
          if (msg.role === "user") return <UserBubble key={i} msg={msg} />;
          if (msg.role === "assistant") return <AssistantBubble key={i} msg={msg} showTools={showTools} reasoningMode={reasoningMode} />;
          if (msg.role === "tool") return showTools ? <ToolResultBubble key={i} msg={msg} allToolCalls={allToolCalls} /> : null;
          return null;
        })}
        {responseMsg && (
          <>
            <div className="flex items-center gap-2 py-1">
              <div className="flex-1 h-px bg-gray-800" />
              <span className="text-[10px] text-gray-600 uppercase tracking-wider">response</span>
              <div className="flex-1 h-px bg-gray-800" />
            </div>
            {responseMsg === "error" ? (
              <div className="rounded-2xl rounded-tl-sm border border-red-900/60 bg-red-950/30 px-4 py-3 max-w-[85%]">
                <div className="text-[10px] font-bold uppercase tracking-wider text-red-400 mb-1.5">
                  error {responseStatus}
                </div>
                <div className="text-xs text-gray-300 leading-5 font-mono whitespace-pre-wrap break-all">
                  <LongString text={typeof responseBody === "string" ? responseBody : JSON.stringify(responseBody, null, 2)} limit={2000} />
                </div>
              </div>
            ) : (
              <AssistantBubble msg={responseMsg} />
            )}
          </>
        )}
      </div>
    </div>
  );
}
