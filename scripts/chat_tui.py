#!/usr/bin/env python3
"""Terminal chat UI for the OpenAI-compatible proxy.

Answers stream in as Markdown; a model's thinking goes in a collapsible
section above its answer (open while it thinks, folded once the answer
starts; click it or press ctrl+t to expand). Each reply ends with token and
speed stats.

Run with `uv run scripts/chat_tui.py` (Textual is a dev dependency).
ctrl+o (or /model with no name) opens the model browser.
Commands: /model [NAME], /clear, /quit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

import httpx
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Collapsible, Footer, Header, Input, Markdown, OptionList, Static
from textual.widgets.option_list import Option

DEFAULT_SERVER = "http://127.0.0.1:1234"

STATE_STYLE = {
    "running": "bold green",
    "starting": "yellow",
    "stopping": "yellow",
    "error": "red",
}
STATE_ORDER = {"running": 0, "starting": 1, "stopping": 2, "stopped": 3, "error": 4}


class ModelBrowser(ModalScreen[str | None]):
    """Pick a model from the proxy's /v1/models. Type to filter (all words
    must match), arrows to move, enter to pick, esc to cancel.

    The llama-manager proxy tags each model as a server (with its state) or
    a library file loaded on request; other servers get one flat list."""

    BINDINGS = [
        Binding("escape", "dismiss_none", "Cancel"),
        Binding("down", "move(1)", show=False, priority=True),
        Binding("up", "move(-1)", show=False, priority=True),
        Binding("pagedown", "move(10)", show=False, priority=True),
        Binding("pageup", "move(-10)", show=False, priority=True),
    ]
    DEFAULT_CSS = """
    ModelBrowser { align: center middle; }
    #browser {
        width: 90%;
        max-width: 110;
        height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 0 1;
    }
    #browser OptionList { height: 1fr; border: none; padding: 0; }
    #browser-status { color: $text-muted; }
    """

    def __init__(self, client: httpx.AsyncClient, server: str, current: str) -> None:
        super().__init__()
        self.client = client
        self.server = server
        self.current = current
        self.models: list[dict] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="browser"):
            yield Input(placeholder="Filter models", id="filter")
            yield OptionList(id="models")
            yield Static("Loading models...", id="browser-status", markup=False)

    def on_mount(self) -> None:
        self.query_one("#browser", Vertical).border_title = "Models"
        self.query_one("#filter", Input).focus()
        self.load()

    @work(exclusive=True)
    async def load(self) -> None:
        status = self.query_one("#browser-status", Static)
        try:
            resp = await self.client.get(f"{self.server}/v1/models", timeout=15)
            resp.raise_for_status()
            self.models = resp.json().get("data") or []
        except (httpx.HTTPError, ValueError) as exc:
            status.update(f"Could not list models: {exc or type(exc).__name__}")
            return
        self.refill(highlight_current=True)

    def refill(self, highlight_current: bool = False) -> None:
        words = self.query_one("#filter", Input).value.lower().split()
        shown = [m for m in self.models
                 if all(w in m.get("id", "").lower() for w in words)]
        servers = [m for m in shown if (m.get("llama_manager") or {}).get("kind") == "server"]
        library = [m for m in shown if (m.get("llama_manager") or {}).get("kind") == "library"]
        other = [m for m in shown if m not in servers and m not in library]
        servers.sort(key=lambda m: (STATE_ORDER.get(m["llama_manager"].get("state"), 5), m["id"].lower()))
        library.sort(key=lambda m: m["id"].lower())

        options: list[Option] = []
        for title, group in (("Servers", servers), ("Library  (loads on request)", library), ("Models", other)):
            if not group:
                continue
            if options:
                options.append(Option(Text(""), disabled=True))
            options.append(Option(Text(title, style="bold"), disabled=True))
            for m in group:
                options.append(Option(self._label(m), id=m["id"]))

        olist = self.query_one("#models", OptionList)
        olist.clear_options()
        olist.add_options(options)
        enabled = [i for i, o in enumerate(options) if not o.disabled]
        if enabled:
            index = enabled[0]
            if highlight_current:
                index = next((i for i in enabled if options[i].id == self.current), index)
            olist.highlighted = index
        n = len(enabled)
        self.query_one("#browser-status", Static).update(
            f"{n} of {len(self.models)} models" if words else f"{n} models")

    def _label(self, m: dict) -> Text:
        mark = "> " if m["id"] == self.current else "  "
        label = Text(mark + m["id"])
        state = (m.get("llama_manager") or {}).get("state")
        if state:
            label.append(f"  {state}", style=STATE_STYLE.get(state, "dim"))
        return label

    def on_input_changed(self, event: Input.Changed) -> None:
        self.refill()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        olist = self.query_one("#models", OptionList)
        if olist.highlighted is not None:
            option = olist.get_option_at_index(olist.highlighted)
            if not option.disabled:
                self.dismiss(option.id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_move(self, step: int) -> None:
        # Move among the models only, skipping group headers; clamp at the ends.
        olist = self.query_one("#models", OptionList)
        enabled = [i for i in range(olist.option_count)
                   if not olist.get_option_at_index(i).disabled]
        if not enabled:
            return
        cur = olist.highlighted
        pos = enabled.index(cur) if cur in enabled else (-1 if step > 0 else len(enabled))
        olist.highlighted = enabled[max(0, min(len(enabled) - 1, pos + step))]

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class UserMessage(Static):
    pass


class AssistantMessage(Vertical):
    """One reply: optional thinking section, Markdown answer, stats line."""

    def __init__(self) -> None:
        super().__init__()
        self.thinking_text = ""
        self.thinking_body = Static("", markup=False, classes="thinking-body")
        self.thinking = Collapsible(self.thinking_body, title="Thinking...", collapsed=False)
        self.thinking.display = False
        self.answer = Markdown("")
        self.answer.display = False
        self.status = Static("Waiting for the model...", markup=False, classes="status")

    def compose(self) -> ComposeResult:
        yield self.thinking
        yield self.answer
        yield self.status

    def add_thinking(self, piece: str) -> None:
        if not self.thinking.display:
            self.thinking.display = True
            self.status.update("Thinking...")
            piece = piece.lstrip()
        self.thinking_text += piece
        self.thinking_body.update(self.thinking_text.rstrip())

    def end_thinking(self, seconds: float | None) -> None:
        if not self.thinking.display:
            return
        took = f" for {seconds:.1f}s" if seconds is not None else ""
        self.thinking.title = f"Thought{took}"
        self.thinking.collapsed = True

    def show_answer(self) -> None:
        self.answer.display = True
        self.status.update("")

    def set_status(self, text: str, error: bool = False) -> None:
        self.status.set_class(error, "error")
        self.status.update(text)


def format_stats(finish: str, elapsed: float, usage: dict, timings: dict,
                 gen_time: float | None) -> str:
    """Server timings when it reports them (llama-server does), else an
    estimate from usage counts and when the first token arrived."""
    parts = [f"{elapsed:.1f}s"]
    if timings.get("predicted_per_second") is not None:
        cached = f" +{timings['cache_n']} cached" if timings.get("cache_n") else ""
        parts.append(f"prompt {timings.get('prompt_n', '?')} tok{cached} @ "
                     f"{timings.get('prompt_per_second', 0):.0f} tok/s")
        parts.append(f"generated {timings.get('predicted_n', '?')} tok @ "
                     f"{timings['predicted_per_second']:.1f} tok/s")
    elif usage:
        parts.append(f"prompt {usage.get('prompt_tokens', '?')} tok")
        n = usage.get("completion_tokens")
        if n and gen_time:
            parts.append(f"generated {n} tok @ ~{n / gen_time:.1f} tok/s")
        elif n:
            parts.append(f"generated {n} tok")
    if finish not in ("stop", "?"):
        parts.append(f"finish={finish}")
    return "  ·  ".join(parts)


class ChatApp(App):
    TITLE = "Llama Chat"
    CSS = """
    #chat { height: 1fr; padding: 0 1; }
    UserMessage {
        margin: 1 0 0 6;
        padding: 0 1;
        background: $primary 15%;
        border-left: thick $primary;
    }
    AssistantMessage {
        height: auto;
        margin: 1 6 0 0;
    }
    /* The bar is drawn per section so thinking gets its own color. */
    AssistantMessage > Markdown, AssistantMessage > .status {
        border-left: thick $success;
        padding: 0 1;
    }
    AssistantMessage Collapsible {
        border: none;
        border-left: thick darkorange;
        padding: 0 1;
        margin: 0 0 1 0;
        background: $boost;
    }
    AssistantMessage Collapsible Contents { padding: 0 1 0 2; }
    .thinking-body { color: $text-muted; text-style: italic; }
    AssistantMessage Markdown { margin: 0; background: transparent; }
    .status { color: $text-muted; }
    .status.error { color: $error; }
    #prompt { margin: 1 0 0 0; }
    """
    BINDINGS = [
        Binding("escape", "stop", "Stop reply"),
        Binding("ctrl+o", "browse_models", "Models"),
        Binding("ctrl+t", "toggle_thinking", "Thinking"),
        Binding("ctrl+l", "clear", "Clear"),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+d", "quit", show=False, priority=True),  # Input binds ctrl+d to delete
    ]

    def __init__(self, server: str, model: str, system: str | None, max_tokens: int | None) -> None:
        super().__init__()
        self.server = server.rstrip("/")
        self.model = model
        self.system = system
        self.max_tokens = max_tokens
        self.messages: list[dict] = []
        self.busy = False
        self.client = httpx.AsyncClient(timeout=None)
        self._reset_history()

    def _reset_history(self) -> None:
        self.messages = [{"role": "system", "content": self.system}] if self.system else []

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="chat")
        yield Input(placeholder="Message  (/model [NAME], /clear, /quit)", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = f"{self.model}  @  {self.server}"
        self.query_one("#chat", VerticalScroll).anchor()
        self.query_one("#prompt", Input).focus()

    async def on_unmount(self) -> None:
        await self.client.aclose()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if text.startswith("/"):
            event.input.clear()
            await self._command(text)
            return
        if self.busy:
            self.notify("Still replying. Press Esc to stop it.", severity="warning")
            return
        event.input.clear()
        chat = self.query_one("#chat", VerticalScroll)
        await chat.mount(UserMessage(text, markup=False))
        reply = AssistantMessage()
        await chat.mount(reply)
        self.messages.append({"role": "user", "content": text})
        self.send(reply)

    async def _command(self, text: str) -> None:
        cmd, _, arg = text.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        if cmd in ("/quit", "/exit", "/q"):
            self.exit()
        elif cmd == "/clear":
            await self.action_clear()
        elif cmd in ("/model", "/models"):
            if arg:
                self.set_model(arg)
            else:
                self.action_browse_models()
        else:
            self.notify(f"Unknown command {cmd}", severity="error")

    @work(exclusive=True, group="chat")
    async def send(self, reply: AssistantMessage) -> None:
        self.busy = True
        body: dict = {
            "model": self.model,
            "messages": self.messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens

        t0 = time.monotonic()
        t_think: float | None = None
        t_answer: float | None = None
        content: list[str] = []
        usage: dict = {}
        timings: dict = {}
        finish = "?"
        stream = None
        ok = False
        try:
            async with self.client.stream(
                "POST", f"{self.server}/v1/chat/completions", json=body
            ) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    try:
                        detail = resp.json()["error"]["message"]
                    except (ValueError, KeyError, TypeError):
                        detail = resp.text[:400]
                    raise RuntimeError(f"HTTP {resp.status_code}: {detail}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    usage = chunk.get("usage") or usage
                    timings = chunk.get("timings") or timings
                    choice = (chunk.get("choices") or [{}])[0]
                    finish = choice.get("finish_reason") or finish
                    delta = choice.get("delta") or {}

                    # llama-server sends reasoning_content; vLLM and others send reasoning.
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    if reasoning:
                        if t_think is None:
                            t_think = time.monotonic()
                        reply.add_thinking(reasoning)

                    token = delta.get("content") or ""
                    if not content:
                        token = token.lstrip()  # models often open with a newline after thinking
                    if token:
                        if stream is None:
                            t_answer = time.monotonic()
                            reply.end_thinking(t_answer - t_think if t_think else None)
                            reply.show_answer()
                            stream = Markdown.get_stream(reply.answer)
                        content.append(token)
                        await stream.write(token)
            ok = True
        except asyncio.CancelledError:
            reply.set_status("Stopped.")
            raise
        except (httpx.HTTPError, RuntimeError) as exc:
            reply.set_status(f"Error: {exc or type(exc).__name__}", error=True)
        finally:
            if stream is not None:
                await stream.stop()
            if t_answer is None:
                reply.end_thinking(time.monotonic() - t_think if t_think else None)
            if content:
                self.messages.append({"role": "assistant", "content": "".join(content)})
            elif self.messages and self.messages[-1]["role"] == "user":
                self.messages.pop()  # nothing came back; let the user retry
            self.busy = False

        if ok:
            elapsed = time.monotonic() - t0
            gen_time = time.monotonic() - (t_think or t_answer) if (t_think or t_answer) else None
            stats = format_stats(finish, elapsed, usage, timings, gen_time)
            reply.set_status(stats if content else f"(empty reply)  ·  {stats}")

    def set_model(self, model: str | None) -> None:
        if not model or model == self.model:
            return
        self.model = model
        self.sub_title = f"{self.model}  @  {self.server}"
        self.notify(f"Model: {model}")

    def action_browse_models(self) -> None:
        if isinstance(self.screen, ModelBrowser):
            return
        self.push_screen(ModelBrowser(self.client, self.server, self.model), self.set_model)

    def action_stop(self) -> None:
        self.workers.cancel_group(self, "chat")

    def action_toggle_thinking(self) -> None:
        sections = [c for c in self.query(Collapsible) if c.display]
        if not sections:
            return
        expand = any(c.collapsed for c in sections)
        for c in sections:
            c.collapsed = not expand

    async def action_clear(self) -> None:
        self.workers.cancel_group(self, "chat")
        self._reset_history()
        await self.query_one("#chat", VerticalScroll).remove_children()


def main() -> None:
    parser = argparse.ArgumentParser(description="Terminal chat UI for the OpenAI-compatible proxy")
    parser.add_argument("-m", "--model", default="gemma-4-31b", metavar="MODEL",
                        help="model ID (default: gemma-4-31b)")
    parser.add_argument("--system", metavar="TEXT", help="system prompt")
    parser.add_argument("--max-tokens", type=int, metavar="N", help="max tokens per reply")
    parser.add_argument("--server", default=DEFAULT_SERVER, metavar="URL",
                        help=f"server base URL (default: {DEFAULT_SERVER})")
    args = parser.parse_args()
    ChatApp(args.server, args.model, args.system, args.max_tokens).run()


if __name__ == "__main__":
    main()
