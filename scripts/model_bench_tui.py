#!/usr/bin/env python3
"""Side-by-side model comparison TUI for the OpenAI-compatible proxy.

Each model gets its own chat column; one input sends every message to all of
them (each model keeps its own conversation). With --judge MODEL, a judge
ranks each round's answers 1..N with a short pro and con per answer, and the
columns turn green (best) to red (worst). Without a judge they stay grey.

Models get tools to list and read articles in a folder (--articles, default
./articles) and to fetch web pages. Each URL is fetched once per run and every
model gets that same copy, so they're compared on the same text. The judge
sees what they read. /tools off (or --no-tools) sends plain chats instead.

Tab switches to a chat with the judge: it sees the settings and the last
round's answers and verdicts, and can change the system prompt and judge
instruction with tools when you ask it to.

Run with `uv run scripts/model_bench_tui.py MODEL [MODEL ...]`.
-n N limits how many models answer at once (default: all of them).

The system prompt and judge instruction are remembered between runs in
~/.cache/model_bench/prefs.json. --system and --judge-instruction override
them and are remembered too.

ctrl+c stops a reply in progress; when nothing is running it clears the
input, and on an empty input it quits.

Commands:
  /add [MODEL]          add a model (no name: pick from the proxy's models)
  /remove [MODEL]       remove a model (no name: pick one of the columns)
  /system [TEXT]        set the system prompt (no text: edit it in a dialog)
  /judge [MODEL|off]    set or turn off the judge (no name: pick one)
  /instruction [TEXT]   set the judge instruction (no text: edit it in a dialog)
  /tools [on|off]       give the models article tools, or not (no arg: toggle)
  /clear                clear the conversations in the current view
  /help, /quit
"""

from __future__ import annotations

import argparse
import asyncio
import colorsys
import json
import random
import string
import sys
import time
from pathlib import Path

import httpx
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import HorizontalScroll, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Collapsible, ContentSwitcher, Footer, Header, Input, Markdown, OptionList, Static, TextArea,
)
from textual.widgets.option_list import Option

from bench_tools import TOOLS as ARTICLE_TOOLS, ArticleTools
from chat_tui import DEFAULT_SERVER, AssistantMessage, ModelBrowser, UserMessage, format_stats

DEFAULT_INSTRUCTION = "Judge on correctness, helpfulness and clarity."
PREFS_FILE = Path("~/.cache/model_bench/prefs.json").expanduser()
GREY = Color.parse("#4a4a4a")
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "rankings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "response": {"type": "string"},
                    "rank": {"type": "integer"},
                    "pro": {"type": "string"},
                    "con": {"type": "string"},
                },
                "required": ["response", "rank", "pro", "con"],
            },
        },
    },
    "required": ["rankings"],
}


MAX_TOOL_STEPS = 5  # judge chat: model turns per message, so a tool loop can't run forever
ANSWER_CHARS = 6000  # judge chat: how much of each answer from the last round it sees
JUDGE_SOURCE_CHARS = 30_000  # judging: how much of each article the models read the judge sees
JUDGE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_system_prompt",
            "description": "Replace the system prompt that every model in the bench gets. "
                           "An empty string clears it. Applies from the next round.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "the new system prompt"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_judge_instruction",
            "description": "Replace the instruction that guides how you rank the models' answers. "
                           "An empty string restores the default. Applies from the next round.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "the new judging instruction"}},
                "required": ["text"],
            },
        },
    },
]


def follow(chat: VerticalScroll) -> None:
    """Keep up with new output while a chat overflows. An anchored chat that
    fits is laid out bottom-aligned, and side-by-side columns read better
    top-aligned, so anchor only while it doesn't fit (it can shrink again when
    a thinking section folds)."""
    overflows = chat.virtual_size.height > chat.size.height
    if overflows != chat.is_anchored:
        chat.anchor(overflows)


def load_prefs() -> dict:
    """Saved preferences, or {} when there are none or they can't be read."""
    try:
        prefs = json.loads(PREFS_FILE.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        print(f"Ignoring unreadable {PREFS_FILE}: {exc}", file=sys.stderr)
        return {}
    return prefs if isinstance(prefs, dict) else {}


def save_prefs(system: str | None, instruction: str) -> None:
    """Write the preferences atomically (a crash mid-write can't corrupt them).
    The default instruction is stored as null so a new default applies."""
    prefs = load_prefs()
    prefs["system"] = system
    prefs["judge_instruction"] = None if instruction == DEFAULT_INSTRUCTION else instruction
    PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PREFS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(prefs, indent=2) + "\n")
    tmp.replace(PREFS_FILE)


def rank_color(rank: int, total: int) -> Color:
    """Green for rank 1, red for the last, hues in between."""
    t = (rank - 1) / (total - 1) if total > 1 else 0.0
    r, g, b = colorsys.hls_to_rgb((1 - t) * 120 / 360, 0.36, 0.70)
    return Color(int(r * 255), int(g * 255), int(b * 255))


def response_labels(n: int) -> list[str]:
    letters = string.ascii_uppercase
    return [letters[i] if i < 26 else f"{letters[i // 26 - 1]}{letters[i % 26]}" for i in range(n)]


def error_detail(resp: httpx.Response) -> str:
    try:
        return resp.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return resp.text[:400]


def parse_json_object(text: str) -> dict:
    """The judge's JSON, tolerating code fences or text around it."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the judge's reply")
    return json.loads(text[start:end + 1])


class ListPicker(ModalScreen[str | None]):
    """Pick one of a short list of names. Enter picks, esc cancels."""

    BINDINGS = [Binding("escape", "dismiss_none", "Cancel")]
    DEFAULT_CSS = """
    ListPicker { align: center middle; }
    #picker {
        width: 60%;
        max-width: 80;
        height: auto;
        max-height: 70%;
        border: thick $primary;
        background: $surface;
        padding: 0 1;
    }
    #picker OptionList { height: auto; max-height: 30; border: none; padding: 0; }
    """

    def __init__(self, title: str, names: list[str]) -> None:
        super().__init__()
        self.title_text = title
        self.names = names

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield OptionList(*(Option(n, id=n) for n in self.names))

    def on_mount(self) -> None:
        self.query_one("#picker", Vertical).border_title = self.title_text
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class TextEditor(ModalScreen[str | None]):
    """Edit a multi-line text. ctrl+s saves, esc cancels (dismisses None)."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("escape", "dismiss_none", "Cancel"),
    ]
    DEFAULT_CSS = """
    TextEditor { align: center middle; }
    #editor {
        width: 80%;
        max-width: 120;
        height: 60%;
        border: thick $primary;
        background: $surface;
        padding: 0 1;
    }
    #editor TextArea { height: 1fr; }
    #editor-help { color: $text-muted; }
    """

    def __init__(self, title: str, text: str, empty_hint: str = "save it empty to clear") -> None:
        super().__init__()
        self.title_text = title
        self.text = text
        self.empty_hint = empty_hint

    def compose(self) -> ComposeResult:
        with Vertical(id="editor"):
            yield TextArea(self.text, soft_wrap=True, show_line_numbers=False)
            yield Static(f"ctrl+s save  ·  esc cancel  ·  {self.empty_hint}", id="editor-help")

    def on_mount(self) -> None:
        self.query_one("#editor", Vertical).border_title = self.title_text
        area = self.query_one(TextArea)
        area.focus()
        area.move_cursor(area.document.end)

    def action_save(self) -> None:
        self.dismiss(self.query_one(TextArea).text.strip())

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class ModelColumn(Vertical):
    """One model's conversation: a colored title bar over its chat."""

    def __init__(self, model: str) -> None:
        super().__init__(classes="column")
        self.model = model
        self.messages: list[dict] = []  # this model's own turns, without the system prompt
        self.title_bar = Static(model, markup=False, classes="title")
        self.chat = VerticalScroll(classes="chat")
        self.verdict: tuple[int, int] | None = None  # (rank, total) for the last round

    def compose(self) -> ComposeResult:
        yield self.title_bar
        yield self.chat

    def on_mount(self) -> None:
        self.set_verdict(None)

    def follow(self) -> None:
        follow(self.chat)

    async def clear(self) -> None:
        self.messages.clear()
        self.set_verdict(None)
        self.chat.anchor(False)
        await self.chat.remove_children()

    def set_verdict(self, verdict: tuple[int, int] | None, note: str = "") -> None:
        self.verdict = verdict
        if verdict is None:
            color = GREY
            label = self.model + (f"  ·  {note}" if note else "")
        else:
            rank, total = verdict
            color = rank_color(rank, total)
            label = f"#{rank} of {total}  ·  {self.model}"
        self.title_bar.update(label)
        self.title_bar.styles.background = color
        self.styles.border = ("round", color)


class BenchApp(App):
    TITLE = "Model Bench"
    CSS = """
    #columns { height: 1fr; }
    .column { width: 1fr; min-width: 44; height: 1fr; margin: 0 0 0 1; }
    .title { width: 1fr; padding: 0 1; color: white; text-style: bold; }
    .chat { height: 1fr; padding: 0 1; }
    UserMessage {
        margin: 1 0 0 2;
        padding: 0 1;
        background: $primary 15%;
        border-left: thick $primary;
    }
    AssistantMessage { height: auto; margin: 1 0 0 0; }
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
    .verdict { margin: 1 0 0 0; padding: 0 1; background: $boost; }
    #empty { width: 1fr; height: 1fr; content-align: center middle; color: $text-muted; }
    #judge-bar { height: auto; max-height: 4; padding: 0 1; color: $text-muted; background: $boost; }
    #judge-bar.error { color: $error; }
    #prompt { margin: 0; }
    #views { height: 1fr; }
    #judge-chat { height: 1fr; padding: 0 1; border: round $accent; }
    #judge-chat AssistantMessage { margin: 1 8 0 0; }
    #judge-chat UserMessage { margin: 1 0 0 8; }
    #judge-chat .tool-note { margin: 1 8 0 0; }
    .tool-note { margin: 1 0 0 0; padding: 0 1; color: $text-muted; background: $boost; border-left: thick $accent; }
    .tool-note.error { color: $error; }
    .hint { color: $text-muted; margin: 1 0 0 0; }
    """
    BINDINGS = [
        Binding("tab", "toggle_view", "Bench/Judge", priority=True),
        Binding("escape", "stop", "Stop"),
        Binding("ctrl+o", "add_model", "Add model"),
        Binding("ctrl+t", "toggle_thinking", "Thinking"),
        Binding("ctrl+l", "clear", "Clear"),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+d", "quit", show=False, priority=True),  # Input binds ctrl+d to delete
        Binding("ctrl+c", "interrupt", "Stop/Clear/Quit", show=False, priority=True),  # over Input's copy
    ]

    def __init__(self, server: str, models: list[str], system: str | None, max_tokens: int | None,
                 parallel: int | None, judge: str | None, instruction: str,
                 articles_dir: Path, tools: bool = True) -> None:
        super().__init__()
        self.server = server.rstrip("/")
        self.initial_models = models
        self.system = system
        self.max_tokens = max_tokens
        self.parallel = parallel
        self.judge = judge
        self.instruction = instruction
        self.busy = False
        self.judge_busy = False
        self.judge_messages: list[dict] = []  # judge chat, without its system message
        # The latest round: {"prompt", "system", "results": {model: {"answer", "rank", "total", "pro", "con"}}}
        self.last_round: dict | None = None
        self.client = httpx.AsyncClient(timeout=None)
        self.tools_enabled = tools
        self.article_tools = ArticleTools(articles_dir, self.client)  # one per run: fetches are shared

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with ContentSwitcher(initial="columns", id="views"):
            yield HorizontalScroll(id="columns")
            yield VerticalScroll(id="judge-chat")
        yield Static("", id="judge-bar", markup=False)
        yield Input(placeholder=self.BENCH_PLACEHOLDER, id="prompt")
        yield Footer()

    BENCH_PLACEHOLDER = "Message all models  (Tab: judge chat, /add, /remove, /system, /judge, /instruction, /help)"
    JUDGE_PLACEHOLDER = "Message the judge  (Tab: back to the bench, /clear, /help)"

    async def on_mount(self) -> None:
        self.query_one("#judge-chat", VerticalScroll).border_title = "Judge chat"
        await self.query_one("#judge-chat", VerticalScroll).mount(Static(
            "Talk with the judge about the last round, or ask it to change the system prompt or "
            "judging instruction.", classes="hint"))
        for model in self.initial_models:
            await self.add_model(model)
        await self._refresh_empty()
        self._update_header()
        self._update_judge_bar()
        self.query_one("#prompt", Input).focus()

    async def on_unmount(self) -> None:
        await self.client.aclose()

    @property
    def in_judge_view(self) -> bool:
        return self.query_one("#views", ContentSwitcher).current == "judge-chat"

    @property
    def columns(self) -> list[ModelColumn]:
        return list(self.query(ModelColumn))

    def _update_header(self) -> None:
        n = len(self.columns)
        at_once = min(self.parallel, n) if self.parallel else n
        if self.in_judge_view:
            self.sub_title = f"Judge chat with {self.judge or '(no judge set)'}  @  {self.server}"
        else:
            tools = "tools on" if self.tools_enabled else "tools off"
            self.sub_title = f"{n} model{'s' if n != 1 else ''}, {at_once} at a time, {tools}  @  {self.server}"

    def _update_judge_bar(self, status: str = "", error: bool = False) -> None:
        bar = self.query_one("#judge-bar", Static)
        bar.set_class(error, "error")
        system = f"System: {self.system}" if self.system else "No system prompt"
        if self.judge:
            judge = f"Judge: {self.judge}  ·  {self.instruction}"
        else:
            judge = "No judge  (/judge MODEL)"
        bar.update(f"{system}\n{judge}" + (f"\n{status}" if status else ""))

    async def _refresh_empty(self) -> None:
        box = self.query_one("#columns", HorizontalScroll)
        empty = self.query("#empty")
        if self.columns and empty:
            await empty.remove()
        elif not self.columns and not empty:
            await box.mount(Static("No models. Type /add or press ctrl+o.", id="empty"))

    # ------------------------------------------------------------------
    # Input and commands
    # ------------------------------------------------------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if text.startswith("/"):
            event.input.clear()
            await self._command(text)
            return
        if self.in_judge_view:
            await self._send_to_judge(event.input, text)
            return
        if self.busy:
            self.notify("Still running. Press Esc to stop the round.", severity="warning")
            return
        columns = self.columns
        if not columns:
            self.notify("Add a model first: /add MODEL", severity="warning")
            return
        event.input.clear()
        entries: list[tuple[ModelColumn, AssistantMessage]] = []
        for col in columns:
            col.set_verdict(None)
            await col.chat.mount(UserMessage(text, markup=False))
            reply = AssistantMessage()
            await col.chat.mount(reply)
            col.messages.append({"role": "user", "content": text})
            col.call_after_refresh(col.follow)
            entries.append((col, reply))
        self.run_round(text, entries)

    async def _command(self, text: str) -> None:
        cmd, _, arg = text.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        if cmd in ("/quit", "/exit", "/q"):
            self.exit()
        elif cmd == "/help":
            self.notify(__doc__.split("Commands:\n", 1)[1].rstrip(), title="Commands", timeout=15)
        elif cmd == "/clear":
            await self.action_clear()
        elif cmd == "/add":
            if arg:
                await self.add_model(arg)
            else:
                self.action_add_model()
        elif cmd in ("/remove", "/rm"):
            if self.busy:
                self.notify("Wait for the round to finish (or Esc) before removing a model.", severity="warning")
            elif arg:
                await self.remove_model(arg)
            elif self.columns:
                self.push_screen(ListPicker("Remove model", [c.model for c in self.columns]), self._remove_picked)
            else:
                self.notify("No models to remove.", severity="warning")
        elif cmd == "/system":
            if arg:
                self.set_system(arg)
            else:
                self.push_screen(TextEditor("System prompt", self.system or ""), self._system_edited)
        elif cmd == "/judge":
            if arg.lower() in ("off", "none"):
                self.set_judge(None)
            elif arg:
                self.set_judge(arg)
            else:
                self.push_screen(ModelBrowser(self.client, self.server, self.judge or ""), self._judge_picked)
        elif cmd == "/tools":
            if arg.lower() in ("on", "off"):
                self.tools_enabled = arg.lower() == "on"
            elif arg:
                self.notify("Usage: /tools [on|off]", severity="error")
                return
            else:
                self.tools_enabled = not self.tools_enabled
            where = self.article_tools.articles_dir
            self.notify(f"Tools on (articles: {where})" if self.tools_enabled else "Tools off")
            self._update_header()
        elif cmd in ("/instruction", "/instruct"):
            if arg:
                self.set_instruction(arg)
            else:
                self.push_screen(TextEditor("Judge instruction", self.instruction, "save it empty for the default"), self._instruction_edited)
        else:
            self.notify(f"Unknown command {cmd}  (/help)", severity="error")

    async def add_model(self, model: str | None) -> None:
        if not model:
            return
        if any(c.model == model for c in self.columns):
            self.notify(f"{model} is already here", severity="warning")
            return
        await self._refresh_empty()
        await self.query_one("#columns", HorizontalScroll).mount(ModelColumn(model))
        await self._refresh_empty()
        self._update_header()

    async def remove_model(self, model: str | None) -> None:
        col = next((c for c in self.columns if c.model == model), None)
        if col is None:
            if model:
                self.notify(f"{model} isn't one of the models", severity="error")
            return
        await col.remove()
        await self._refresh_empty()
        self._update_header()

    async def _remove_picked(self, model: str | None) -> None:
        await self.remove_model(model)

    def set_system(self, text: str) -> None:
        self.system = text or None
        self.notify("System prompt set" if text else "System prompt cleared")
        self._update_judge_bar()
        self._save_prefs()

    def _save_prefs(self) -> None:
        try:
            save_prefs(self.system, self.instruction)
        except OSError as exc:
            self.notify(f"Couldn't save preferences: {exc}", severity="warning")

    def _system_edited(self, text: str | None) -> None:
        if text is not None and text != (self.system or ""):  # None: cancelled
            self.set_system(text)

    def set_instruction(self, text: str) -> None:
        # The judge always needs some guidance: clearing restores the default.
        self.instruction = text or DEFAULT_INSTRUCTION
        self.notify("Judge instruction set" if text else "Judge instruction reset to the default")
        self._update_judge_bar()
        self._save_prefs()

    def _instruction_edited(self, text: str | None) -> None:
        if text is not None and text != self.instruction:  # None: cancelled
            self.set_instruction(text)

    def _judge_picked(self, model: str | None) -> None:
        if model:
            self.set_judge(model)

    def set_judge(self, model: str | None) -> None:
        self.judge = model
        if model is None:
            for col in self.columns:
                col.set_verdict(None)
        self.notify(f"Judge: {model}" if model else "Judge off")
        self._update_judge_bar()
        self._update_header()

    def action_add_model(self) -> None:
        if isinstance(self.screen, (ModelBrowser, ListPicker, TextEditor)):
            return
        self.push_screen(ModelBrowser(self.client, self.server, ""), self.add_model)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in ("toggle_view", "interrupt") and self.screen is not self.screen_stack[0]:
            return False  # a dialog is open: let it have Tab and ctrl+c
        return True

    def action_toggle_view(self) -> None:
        judge = not self.in_judge_view
        self.query_one("#views", ContentSwitcher).current = "judge-chat" if judge else "columns"
        prompt = self.query_one("#prompt", Input)
        prompt.placeholder = self.JUDGE_PLACEHOLDER if judge else self.BENCH_PLACEHOLDER
        prompt.focus()
        self._update_header()
        if judge:
            follow(self.query_one("#judge-chat", VerticalScroll))

    def action_interrupt(self) -> None:
        """ctrl+c: stop what's generating (this view's first), else clear the
        input, else quit."""
        groups = {"bench": self.busy, "judge-chat": self.judge_busy}
        here = "judge-chat" if self.in_judge_view else "bench"
        for group in sorted(groups, key=lambda g: g != here):
            if groups[group]:
                self.workers.cancel_group(self, group)
                return
        prompt = self.query_one("#prompt", Input)
        if prompt.value:
            prompt.clear()
        else:
            self.exit()

    def action_stop(self) -> None:
        self.workers.cancel_group(self, "judge-chat" if self.in_judge_view else "bench")

    def action_toggle_thinking(self) -> None:
        sections = [c for c in self.query(Collapsible) if c.display]
        if not sections:
            return
        expand = any(c.collapsed for c in sections)
        for c in sections:
            c.collapsed = not expand

    async def action_clear(self) -> None:
        if self.in_judge_view:
            self.workers.cancel_group(self, "judge-chat")
            self.judge_messages.clear()
            chat = self.query_one("#judge-chat", VerticalScroll)
            chat.anchor(False)
            await chat.remove_children()
            return
        self.workers.cancel_group(self, "bench")
        for col in self.columns:
            await col.clear()
        self._update_judge_bar()

    # ------------------------------------------------------------------
    # Rounds
    # ------------------------------------------------------------------

    @work(exclusive=True, group="bench")
    async def run_round(self, prompt: str, entries: list[tuple[ModelColumn, AssistantMessage]]) -> None:
        self.busy = True
        sem = asyncio.Semaphore(self.parallel or len(entries))
        system = self.system
        tools = self.tools_enabled
        sources: dict[str, str] = {}  # what the models read this round, for the judge

        async def one(col: ModelColumn, reply: AssistantMessage) -> str | None:
            reply.set_status("Queued...")
            async with sem:
                reply.set_status("Waiting for the model...")
                return await self.run_model(col, reply, system, tools, sources)

        try:
            self._update_judge_bar("Running...")
            answers = await asyncio.gather(*(one(c, r) for c, r in entries))
            answered = [(col, a) for (col, _), a in zip(entries, answers) if a]
            self.last_round = {
                "prompt": prompt,
                "system": system,
                "results": {col.model: {"answer": a} for col, a in answered},
                "sources": sources,
            }
            if not self.judge:
                self._update_judge_bar()
            elif len(answered) < 2:
                self._update_judge_bar("Not judged: fewer than two models answered.")
            else:
                await self.judge_round(prompt, system, answered, {id(c): r for c, r in entries}, sources)
        except asyncio.CancelledError:
            self._update_judge_bar("Stopped.")
            raise
        finally:
            self.busy = False

    async def run_model(self, col: ModelColumn, reply: AssistantMessage, system: str | None,
                        tools: bool, sources: dict[str, str]) -> str | None:
        """One model's turn: stream its reply into its column, running any
        tool calls and continuing until it answers without one. Returns the
        answer, or None. The turn stays in its history only if it finished,
        so a failed or stopped turn can be retried cleanly."""
        start = len(col.messages) - 1  # its user message
        answer: str | None = None
        try:
            for _ in range(MAX_TOOL_STEPS):
                body: dict = {
                    "model": col.model,
                    "messages": ([{"role": "system", "content": system}] if system else []) + col.messages,
                }
                if tools:
                    body["tools"] = ARTICLE_TOOLS
                if self.max_tokens is not None:
                    body["max_tokens"] = self.max_tokens
                content, tool_calls, ok = await self.stream_completion(body, reply, col.chat)
                if not ok or not (content or tool_calls):
                    return None
                message: dict = {"role": "assistant", "content": content}
                if tool_calls:
                    message["tool_calls"] = tool_calls
                col.messages.append(message)
                if not tool_calls:
                    answer = content
                    return answer
                for call in tool_calls:
                    note = Static(f"{call['function']['name']}...", markup=False, classes="tool-note")
                    await col.chat.mount(note)
                    result = await self.article_tools.run(call["function"]["name"], call["function"]["arguments"])
                    note.update(result.note)
                    note.set_class(result.error, "error")
                    if result.source is not None:
                        sources.setdefault(*result.source)
                    col.messages.append({"role": "tool", "tool_call_id": call["id"], "content": result.content})
                reply = AssistantMessage()
                await col.chat.mount(reply)
                col.call_after_refresh(col.follow)
            await col.chat.mount(Static(f"Stopped after {MAX_TOOL_STEPS} tool steps.", classes="tool-note error"))
            return None
        finally:
            if answer is None:
                del col.messages[start:]

    async def stream_completion(self, body: dict, reply: AssistantMessage,
                                chat: VerticalScroll) -> tuple[str, list[dict], bool]:
        """Stream a chat completion into reply: thinking, answer, then stats or
        the error. Returns (answer, tool calls, ok)."""
        body = {**body, "stream": True, "stream_options": {"include_usage": True}}
        t0 = time.monotonic()
        t_think: float | None = None
        t_answer: float | None = None
        content: list[str] = []
        calls: dict[int, dict] = {}  # tool calls arrive in pieces, keyed by index
        usage: dict = {}
        timings: dict = {}
        finish = "?"
        stream = None
        ok = False
        try:
            async with self.client.stream("POST", f"{self.server}/v1/chat/completions", json=body) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    raise RuntimeError(f"HTTP {resp.status_code}: {error_detail(resp)}")
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

                    reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    if reasoning:
                        if t_think is None:
                            t_think = time.monotonic()
                        reply.add_thinking(reasoning)
                        follow(chat)

                    for piece in delta.get("tool_calls") or []:
                        call = calls.setdefault(piece.get("index", len(calls)), {
                            "id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        call["id"] = piece.get("id") or call["id"]
                        fn = piece.get("function") or {}
                        call["function"]["name"] += fn.get("name") or ""
                        call["function"]["arguments"] += fn.get("arguments") or ""

                    token = delta.get("content") or ""
                    if not content:
                        token = token.lstrip()
                    if token:
                        if stream is None:
                            t_answer = time.monotonic()
                            reply.end_thinking(t_answer - t_think if t_think else None)
                            reply.show_answer()
                            stream = Markdown.get_stream(reply.answer)
                            chat.call_after_refresh(follow, chat)
                        content.append(token)
                        await stream.write(token)
                        follow(chat)
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
            chat.call_after_refresh(follow, chat)

        tool_calls = [calls[i] for i in sorted(calls)]
        for i, call in enumerate(tool_calls):
            call["id"] = call["id"] or f"call_{i}"
        if ok:
            elapsed = time.monotonic() - t0
            gen_time = time.monotonic() - (t_think or t_answer) if (t_think or t_answer) else None
            stats = format_stats(finish, elapsed, usage, timings, gen_time)
            reply.set_status(stats if content or tool_calls else f"(empty reply)  ·  {stats}")
        return "".join(content), tool_calls, ok

    # ------------------------------------------------------------------
    # Judge chat
    # ------------------------------------------------------------------

    async def _send_to_judge(self, prompt: Input, text: str) -> None:
        if not self.judge:
            self.notify("Set a judge first: /judge MODEL", severity="warning")
            return
        if self.judge_busy:
            self.notify("The judge is still replying. Press Esc to stop it.", severity="warning")
            return
        prompt.clear()
        chat = self.query_one("#judge-chat", VerticalScroll)
        await chat.mount(UserMessage(text, markup=False))
        chat.call_after_refresh(follow, chat)
        self.judge_messages.append({"role": "user", "content": text})
        self.judge_chat()

    def _judge_chat_system(self) -> str:
        """Fresh every turn, so the judge sees settings it just changed."""
        lines = [
            "You are the judge in Model Bench, a tool that sends the user's prompts to several models "
            "side by side. After each round you rank their answers in a separate request. Here the user "
            "talks with you directly: about the answers, your rankings, and how to set up the bench.",
            "",
            "You can change two settings with tools: set_system_prompt (the system prompt every bench "
            "model gets) and set_judge_instruction (what guides your rankings). Change them only when "
            "the user asks or agrees, and tell them what you set. Changes apply from the next round.",
            "",
            f"Current system prompt: {self.system or '(none)'}",
            f"Current judging instruction: {self.instruction}",
            f"Models in the bench: {', '.join(c.model for c in self.columns) or '(none)'}",
        ]
        rnd = self.last_round
        if rnd is None:
            lines += ["", "No round has run yet."]
        else:
            lines += ["", "Last round:", f"<prompt>\n{rnd['prompt']}\n</prompt>"]
            if rnd["system"] != self.system:
                lines.append(f"(It ran with the system prompt: {rnd['system'] or '(none)'})")
            for model, res in rnd["results"].items():
                answer = res["answer"]
                if len(answer) > ANSWER_CHARS:
                    answer = answer[:ANSWER_CHARS] + "\n[... cut]"
                verdict = (f"\nYour verdict: #{res['rank']} of {res['total']}. Pro: {res['pro']} Con: {res['con']}"
                           if "rank" in res else "\n(Not ranked.)")
                lines.append(f'<answer model="{model}">\n{answer}\n</answer>{verdict}')
            for name, text in rnd.get("sources", {}).items():
                if len(text) > ANSWER_CHARS:
                    text = text[:ANSWER_CHARS] + "\n[... cut]"
                lines.append(f'<source name="{name}" note="read by the models with their tools">\n{text}\n</source>')
        return "\n".join(lines)

    @work(exclusive=True, group="judge-chat")
    async def judge_chat(self) -> None:
        """Get the judge's reply, running any tool calls and replying to them
        until it answers without one (or MAX_TOOL_STEPS is reached)."""
        chat = self.query_one("#judge-chat", VerticalScroll)
        self.judge_busy = True
        try:
            for _ in range(MAX_TOOL_STEPS):
                reply = AssistantMessage()
                await chat.mount(reply)
                chat.call_after_refresh(follow, chat)
                body = {
                    "model": self.judge,
                    "messages": [{"role": "system", "content": self._judge_chat_system()}, *self.judge_messages],
                    "tools": JUDGE_TOOLS,
                }
                content, tool_calls, ok = await self.stream_completion(body, reply, chat)
                if not ok or not (content or tool_calls):
                    if self.judge_messages and self.judge_messages[-1]["role"] == "user":
                        self.judge_messages.pop()  # nothing came back; let the user retry
                    return
                message: dict = {"role": "assistant", "content": content}
                if tool_calls:
                    message["tool_calls"] = tool_calls
                self.judge_messages.append(message)
                if not tool_calls:
                    return
                for call in tool_calls:
                    result, error = self._run_judge_tool(call)
                    note = Static(result, markup=False, classes="tool-note")
                    note.set_class(error, "error")
                    await chat.mount(note)
                    self.judge_messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
                chat.call_after_refresh(follow, chat)
            await chat.mount(Static(f"Stopped after {MAX_TOOL_STEPS} tool steps.", classes="tool-note error"))
        finally:
            self.judge_busy = False

    def _run_judge_tool(self, call: dict) -> tuple[str, bool]:
        """Run one of the judge's tool calls. Returns (result, is_error); the
        result goes back to the judge and is shown in the chat."""
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"]["arguments"] or "{}")
            text = args["text"]
            if not isinstance(text, str):
                raise TypeError("text must be a string")
        except (ValueError, KeyError, TypeError) as exc:
            return f"{name} failed: bad arguments ({exc})", True
        text = text.strip()
        if name == "set_system_prompt":
            self.set_system(text)
            return (f"System prompt set to: {text}" if text else "System prompt cleared"), False
        if name == "set_judge_instruction":
            self.set_instruction(text)
            return (f"Judging instruction set to: {self.instruction}" if text
                    else f"Judging instruction reset to the default: {self.instruction}"), False
        return f"Unknown tool: {name}", True

    async def judge_round(self, prompt: str, system: str | None, answered: list[tuple[ModelColumn, str]],
                          replies: dict[int, AssistantMessage], sources: dict[str, str]) -> None:
        """Ask the judge to rank this round's answers, then color the columns."""
        judge = self.judge
        assert judge is not None
        shuffled = answered[:]
        random.shuffle(shuffled)  # don't let column order bias the judge
        labels = response_labels(len(shuffled))
        by_label = {label: col for label, (col, _) in zip(labels, shuffled)}

        parts = []
        if system:
            parts.append(f"System prompt given to every model:\n<system>\n{system}\n</system>")
        parts.append(f"User prompt:\n<prompt>\n{prompt}\n</prompt>")
        for name, text in sources.items():
            if len(text) > JUDGE_SOURCE_CHARS:
                text = text[:JUDGE_SOURCE_CHARS] + "\n[... cut]"
            parts.append(f'Source the models read with their tools:\n<source name="{name}">\n{text}\n</source>')
        for label, (_, answer) in zip(labels, shuffled):
            parts.append(f'<response id="{label}">\n{answer}\n</response>')
        n = len(shuffled)
        parts.append(
            f"Rank all {n} responses from 1 (best) to {n} (worst), each rank used once. "
            'Reply with JSON only: {"rankings": [{"response": "A", "rank": 1, '
            '"pro": "...", "con": "..."}, ...]}, one entry per response.'
        )
        judge_system = (
            "You judge answers that different AI models gave to the same prompt. Rank them and give a "
            "one-sentence pro and one-sentence con for each. Judge only what is written; ignore length "
            "unless it hurts the answer. When sources are given, check the answers against them."
            f"\n\nJudging instruction: {self.instruction}"
        )
        body: dict = {
            "model": judge,
            "messages": [
                {"role": "system", "content": judge_system},
                {"role": "user", "content": "\n\n".join(parts)},
            ],
            "response_format": {"type": "json_schema", "json_schema": {"name": "ranking", "schema": JUDGE_SCHEMA}},
        }

        self._update_judge_bar(f"Judging with {judge}...")
        t0 = time.monotonic()
        try:
            url = f"{self.server}/v1/chat/completions"
            resp = await self.client.post(url, json=body)
            if resp.status_code == 400:  # server without structured output: ask plainly
                body.pop("response_format")
                resp = await self.client.post(url, json=body)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {error_detail(resp)}")
            content = resp.json()["choices"][0]["message"].get("content") or ""
            rankings = parse_json_object(content).get("rankings")
            if not isinstance(rankings, list):
                raise ValueError("judge reply has no rankings list")
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError, IndexError, TypeError) as exc:
            self._update_judge_bar(f"Judge failed: {exc or type(exc).__name__}", error=True)
            return

        # Trust the judge's order, not its numbers: sort by rank, then renumber
        # 1..m so ties or gaps still give each column a distinct color.
        seen: dict[str, dict] = {}
        for r in rankings:
            if isinstance(r, dict) and str(r.get("response", "")).strip().upper() in by_label:
                seen.setdefault(str(r["response"]).strip().upper(), r)

        def rank_of(item: tuple[str, dict]) -> float:
            try:
                return float(item[1].get("rank"))
            except (TypeError, ValueError):
                return float("inf")

        ordered = sorted(seen.items(), key=rank_of)
        total = len(ordered)
        for rank, (label, r) in enumerate(ordered, start=1):
            col = by_label[label]
            col.set_verdict((rank, total))
            if self.last_round is not None and col.model in self.last_round["results"]:
                self.last_round["results"][col.model].update(
                    rank=rank, total=total, pro=str(r.get("pro", "")).strip(), con=str(r.get("con", "")).strip())
            color = rank_color(rank, total)
            verdict = Text.assemble(
                (f"#{rank} of {total}", f"bold {color.hex}"),
                ("  judged by ", "dim"), (judge, "dim"), "\n",
                ("+ ", "bold green"), str(r.get("pro", "")).strip(), "\n",
                ("- ", "bold red"), str(r.get("con", "")).strip(),
            )
            widget = Static(verdict, classes="verdict")
            widget.styles.border_left = ("thick", color)
            reply = replies.get(id(col))
            if reply is not None and reply.is_attached:
                await col.chat.mount(widget, after=reply)
                col.call_after_refresh(col.follow)
        for label, col in by_label.items():
            if label not in seen:
                col.set_verdict(None, "not ranked by the judge")
        missing = len(by_label) - total
        note = f"  ·  {missing} not ranked" if missing else ""
        self._update_judge_bar(f"Judged by {judge} in {time.monotonic() - t0:.1f}s{note}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send one chat to several models side by side, optionally judged")
    parser.add_argument("models", nargs="*", metavar="MODEL", help="model IDs to compare (more with /add)")
    parser.add_argument("-n", type=int, metavar="N",
                        help="how many models answer at once (default: all of them)")
    parser.add_argument("--system", metavar="TEXT",
                        help="system prompt for every model (default: the last one used)")
    parser.add_argument("--judge", metavar="MODEL", help="model that ranks each round's answers")
    parser.add_argument("--judge-instruction", metavar="TEXT",
                        help=f"what the judge should weigh (default: the last one used, "
                             f"else {DEFAULT_INSTRUCTION!r})")
    parser.add_argument("--max-tokens", type=int, metavar="N", help="max tokens per reply")
    parser.add_argument("--articles", type=Path, default=Path("articles"), metavar="DIR",
                        help="folder the models' read_article tool reads from (default: ./articles)")
    parser.add_argument("--no-tools", action="store_true", help="start with the models' tools off (/tools on)")
    parser.add_argument("--server", default=DEFAULT_SERVER, metavar="URL",
                        help=f"server base URL (default: {DEFAULT_SERVER})")
    args = parser.parse_args()
    if args.n is not None and args.n < 1:
        parser.error("-n must be at least 1")
    models = list(dict.fromkeys(args.models))  # drop repeats, keep order

    prefs = load_prefs()
    system = args.system if args.system is not None else prefs.get("system")
    instruction = args.judge_instruction or prefs.get("judge_instruction") or DEFAULT_INSTRUCTION
    system = system.strip() or None if isinstance(system, str) else None
    if args.system is not None or args.judge_instruction is not None:
        try:
            save_prefs(system, instruction)
        except OSError as exc:
            print(f"Couldn't save preferences to {PREFS_FILE}: {exc}", file=sys.stderr)

    BenchApp(args.server, models, system, args.max_tokens, args.n, args.judge, instruction,
             args.articles.expanduser(), tools=not args.no_tools).run()


if __name__ == "__main__":
    main()
