#!/usr/bin/env python3
"""
Interactive multi-chat slot tester for llama-server.

Each of 8 chat slots has a unique synthetic system prompt (~8 k tokens by
default) so that switching slots forces the server to swap KV-cache contents.

  Press 1-8 on an empty line to switch slots (no Enter needed).
  Type a message + Enter to send to the active slot.
  /clear    reset the current slot's conversation history
  /history  show the current slot's message history
  /quit     exit  (Ctrl-C / Ctrl-D also work)
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import termios
import time
import tty
from contextlib import contextmanager
from dataclasses import dataclass, field

import httpx


# ── Synthetic context ──────────────────────────────────────────────────────────

_SLOT_TOPICS = [
    "mathematics, number theory, and formal logic",
    "world history, political philosophy, and geopolitics",
    "software engineering, distributed systems, and compilers",
    "biology, genetics, and evolutionary ecology",
    "physics, quantum mechanics, and cosmology",
    "literature, linguistics, and narrative craft",
    "macroeconomics, market dynamics, and monetary theory",
    "philosophy of mind, ethics, and epistemology",
]

_FRAGMENTS = [
    "You are a helpful, harmless, and honest AI assistant. Always respond clearly and concisely.",
    "Before answering, break the problem into smaller sub-tasks and reason through each one step by step.",
    "If you are unsure about a fact, say so explicitly rather than guessing or hallucinating details.",
    "Prioritize user safety. Never provide advice that could cause harm to the user or others.",
    "Cite your reasoning when drawing conclusions, especially for complex or ambiguous questions.",
    "When writing code, include comments explaining the intent behind non-obvious logic.",
    "Prefer simple, readable solutions over clever or overly optimized ones unless performance is critical.",
    "If the user's request is ambiguous, ask a clarifying question before proceeding.",
    "Always verify that your outputs satisfy the constraints stated in the prompt.",
    "Decompose multi-step tasks into an explicit plan before executing any individual step.",
    "When summarizing documents, preserve key details and avoid introducing new information.",
    "Maintain a neutral, professional tone unless the user explicitly requests a different style.",
    "For mathematical problems, show all intermediate steps rather than jumping to the final answer.",
    "When comparing options, evaluate trade-offs explicitly across relevant dimensions.",
    "If you detect a logical inconsistency in the user's premises, point it out respectfully.",
    "Avoid repeating verbatim what the user already said; paraphrase or advance the conversation instead.",
    "Use bullet points or numbered lists when presenting multiple independent items for clarity.",
    "For long tasks, periodically summarize progress so the user can follow your reasoning.",
    "Do not make assumptions about the user's background knowledge; explain technical terms when introduced.",
    "When asked to critique or review work, be specific and constructive rather than vague or dismissive.",
    "Validate edge cases and boundary conditions when writing or reviewing algorithms.",
    "When given conflicting instructions, follow the most recent one and note the conflict to the user.",
    "Structure longer responses with headings or clear section breaks to aid readability.",
    "If a task is outside your capabilities, explain clearly what you cannot do and why.",
    "When generating creative content, stay within the themes and constraints specified by the user.",
    "For debugging tasks, hypothesize the most likely root cause before proposing a fix.",
    "Always double-check units, dimensions, and data types when working with numerical problems.",
    "Prefer reversible actions and flag any steps that cannot be undone before taking them.",
    "When translating between languages, preserve the tone and intent of the original text.",
    "If a response would be very long, offer a concise summary and ask if the user wants more detail.",
]


def make_system_prompt(slot: int, target_tokens: int) -> str:
    """Build a unique ~target_tokens-token system prompt for slot 1–8."""
    topic = _SLOT_TOPICS[slot - 1]
    rng = random.Random(slot * 0xDEAD_BEEF)
    fingerprint = f"{rng.getrandbits(128):032x}"

    header = (
        f"=== CHAT SLOT {slot} | {topic.upper()} ===\n"
        f"You are an expert assistant specialising in {topic}.\n"
        f"Session fingerprint: {fingerprint}\n\n"
    )

    target_chars = max(0, target_tokens * 4 - len(header))
    parts: list[str] = [header]
    total = 0
    while total < target_chars:
        frag = rng.choice(_FRAGMENTS)
        parts.append(frag)
        total += len(frag) + 1

    return "\n".join(parts)

_SLOT_QUESTIONS: dict[int, list[str]] = {
    1: [  # mathematics, number theory, and formal logic
        "What is the Riemann hypothesis and why does it matter?",
        "Explain Gödel's incompleteness theorems and their implications.",
        "What is the difference between proof by induction and proof by contradiction?",
        "How does the Euclidean algorithm for finding GCDs work?",
        "Why are prime numbers important in cryptography?",
        "Explain the P vs NP problem.",
        "What is the significance of Euler's identity?",
        "How do transfinite numbers work?",
        "What is a formal language and how does it relate to computation?",
        "Explain the four-color theorem.",
        "What is the Collatz conjecture?",
        "How does modular arithmetic work?",
        "What is the relationship between Fibonacci numbers and the golden ratio?",
        "Explain the concept of mathematical infinity.",
        "What is a group in abstract algebra?",
        "How does Fermat's Last Theorem differ from Fermat's Little Theorem?",
        "Explain what a mathematical proof is and why rigor matters.",
        "What is the pigeonhole principle? Give an example.",
        "How does Boolean algebra relate to computer logic?",
        "What is the halting problem and why is it unsolvable?",
    ],
    2: [  # world history, political philosophy, and geopolitics
        "What were the main causes of the First World War?",
        "How did the fall of Rome shape medieval Europe?",
        "Explain the core ideas of Machiavelli's The Prince.",
        "What is social contract theory and who were its main proponents?",
        "How did the Industrial Revolution transform society?",
        "What factors led to the collapse of the Soviet Union?",
        "Explain the concept of hegemony in international relations.",
        "How did colonialism shape the modern world?",
        "What are the key differences between realism and liberalism in geopolitics?",
        "How did the Black Death affect European civilization?",
        "What was the Enlightenment and why was it significant?",
        "Explain the causes and consequences of the French Revolution.",
        "How does the concept of sovereignty shape modern nation-states?",
        "What role did geography play in the rise of ancient civilizations?",
        "Explain the balance-of-power theory in international relations.",
        "How did the Cold War reshape global politics?",
        "What are the origins of democracy in ancient Athens?",
        "How did the Silk Road influence global trade and culture?",
        "Explain the philosophical differences between liberalism and conservatism.",
        "What factors determine whether a democracy succeeds or fails?",
    ],
    3: [  # software engineering, distributed systems, and compilers
        "What is the CAP theorem and why does it matter?",
        "Explain how a garbage collector works in modern runtimes.",
        "What is the difference between optimistic and pessimistic concurrency control?",
        "How does a compiler transform source code into machine instructions?",
        "Explain the concept of eventual consistency.",
        "What is the difference between a process and a thread?",
        "How does the Paxos consensus algorithm work?",
        "What is a memory barrier and why is it needed?",
        "Explain the concept of a type system and its benefits.",
        "What are the trade-offs between microservices and monolithic architectures?",
        "How does register allocation work in a compiler?",
        "What is a distributed hash table and how is it used?",
        "Explain tail-call optimization.",
        "How do lock-free data structures work?",
        "What is a Bloom filter and when would you use one?",
        "Explain the difference between eager and lazy evaluation.",
        "How does a linker differ from a compiler?",
        "What is the actor model of concurrency?",
        "Explain how consistent hashing works.",
        "What are the main differences between SQL and NoSQL databases?",
    ],
    4: [  # biology, genetics, and evolutionary ecology
        "How does natural selection drive evolution?",
        "Explain the structure and function of DNA.",
        "What is the central dogma of molecular biology?",
        "How do CRISPR-Cas9 systems work for gene editing?",
        "Explain the concept of genetic drift.",
        "What is symbiosis and what are its different forms?",
        "How do ecosystems maintain balance through feedback loops?",
        "What is the role of mitochondria in the cell?",
        "How does the immune system distinguish self from non-self?",
        "What is horizontal gene transfer and why is it important?",
        "How do viruses replicate inside host cells?",
        "Explain the concept of a keystone species.",
        "What is epigenetics and how does it differ from genetics?",
        "How did the Cambrian explosion shape animal diversity?",
        "What are the mechanisms of speciation?",
        "Explain how protein folding works.",
        "What is the nitrogen cycle and why is it important?",
        "How do invasive species affect ecosystems?",
        "What is the endosymbiotic theory of mitochondrial origins?",
        "Explain the difference between sexual and asexual reproduction.",
    ],
    5: [  # physics, quantum mechanics, and cosmology
        "What is wave-particle duality?",
        "Explain the Heisenberg uncertainty principle.",
        "How does quantum entanglement work?",
        "What is dark matter and what evidence do we have for it?",
        "Explain the Big Bang theory.",
        "What is the difference between general and special relativity?",
        "How does a black hole form and what happens at the event horizon?",
        "What is the Schrödinger equation and what does it describe?",
        "Explain the concept of entropy in thermodynamics.",
        "What is the standard model of particle physics?",
        "How does nuclear fusion power the sun?",
        "What is quantum decoherence?",
        "Explain the concept of spacetime curvature.",
        "What is the cosmological constant problem?",
        "How do quantum computers differ from classical computers?",
        "What is Hawking radiation?",
        "Explain the double-slit experiment and its significance.",
        "What is the multiverse hypothesis?",
        "How does the photoelectric effect work?",
        "What is the role of symmetry in physics?",
    ],
    6: [  # literature, linguistics, and narrative craft
        "What is the hero's journey and how is it used in storytelling?",
        "Explain the concept of an unreliable narrator.",
        "How does point of view affect a story's meaning?",
        "What is the difference between syntax and semantics in linguistics?",
        "How do metaphors shape our understanding of abstract concepts?",
        "What makes a character feel real to a reader?",
        "Explain the Sapir-Whorf hypothesis.",
        "How does stream-of-consciousness narration work?",
        "What is the difference between showing and telling in fiction?",
        "How do poetic meter and rhythm create meaning?",
        "What is intertextuality and how does it work?",
        "How does language change over time?",
        "How does genre shape reader expectations?",
        "What is magical realism as a literary mode?",
        "How does dialogue reveal character in fiction?",
        "What is the role of ambiguity in literary interpretation?",
        "Explain Chomsky's theory of universal grammar.",
        "How does narrative structure differ across cultures?",
        "What is the difference between theme and motif?",
        "How does irony function in literature?",
    ],
    7: [  # macroeconomics, market dynamics, and monetary theory
        "What is the quantity theory of money?",
        "Explain the concept of opportunity cost.",
        "How does inflation affect purchasing power?",
        "What is the difference between fiscal and monetary policy?",
        "Explain the Phillips curve and its limitations.",
        "How do central banks control the money supply?",
        "What is a market bubble and how does it form?",
        "Explain the concept of comparative advantage.",
        "How does the interest rate affect investment and consumption?",
        "What is the difference between GDP and GNP?",
        "Explain the concept of moral hazard in finance.",
        "How do exchange rates affect international trade?",
        "What is the liquidity trap?",
        "Explain the concept of externalities and market failure.",
        "How does Keynesian economics differ from monetarism?",
        "What is quantitative easing and how does it work?",
        "Explain game theory's role in economics.",
        "What is the role of trust in financial systems?",
        "How does income inequality affect economic growth?",
        "What is the efficient market hypothesis?",
    ],
    8: [  # philosophy of mind, ethics, and epistemology
        "What is the mind-body problem?",
        "Explain Descartes' cogito ergo sum.",
        "What is the difference between deontological and consequentialist ethics?",
        "How does Kant's categorical imperative work?",
        "What is the trolley problem and what does it reveal about moral intuitions?",
        "Explain the concept of qualia in philosophy of mind.",
        "What is the difference between knowledge and belief?",
        "How does Plato's allegory of the cave relate to epistemology?",
        "What is functionalism in philosophy of mind?",
        "Explain the Gettier problem.",
        "What is the difference between free will and determinism?",
        "How does utilitarianism handle conflicts between individual and group welfare?",
        "What is the Chinese Room argument?",
        "Explain the concept of epistemic justification.",
        "How does virtue ethics differ from rule-based ethics?",
        "What is the hard problem of consciousness?",
        "Explain the concept of moral relativism.",
        "How do we justify inductive reasoning?",
        "What is the problem of other minds?",
        "How does Rawls' veil of ignorance work?",
    ],
}


# ── Chat state ─────────────────────────────────────────────────────────────────

@dataclass
class Chat:
    slot: int
    system_prompt: str
    history: list[dict] = field(default_factory=list)

    @property
    def turns(self) -> int:
        return len(self.history) // 2

    @property
    def topic(self) -> str:
        return _SLOT_TOPICS[self.slot - 1]

    def build_messages(self, user_input: str) -> list[dict]:
        return (
            [{"role": "system", "content": self.system_prompt}]
            + self.history
            + [{"role": "user", "content": user_input}]
        )

    def append_turn(self, user: str, assistant: str) -> None:
        self.history.append({"role": "user", "content": user})
        self.history.append({"role": "assistant", "content": assistant})

    def clear(self) -> None:
        self.history.clear()


# ── Stream result ──────────────────────────────────────────────────────────────

@dataclass
class StreamResult:
    text: str
    ttft: float
    elapsed: float
    completion_tokens: int
    prompt_tokens: int

    def format_stats(self) -> str:
        tps = self.completion_tokens / self.elapsed if self.elapsed > 0 else 0.0
        parts = [
            f"ttft={self.ttft * 1000:.0f}ms",
            f"total={self.elapsed:.2f}s",
            f"tokens={self.completion_tokens}",
            f"speed={tps:.1f} tok/s",
        ]
        if self.prompt_tokens:
            parts.append(f"prompt={self.prompt_tokens} tok")
        return f"▶ {' · '.join(parts)}"


# ── Completion client ──────────────────────────────────────────────────────────

class CompletionClient:
    def __init__(self, base_url: str, max_tokens: int, model: str | None = None) -> None:
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.model = model
        self._client = httpx.Client()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CompletionClient:
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def stream(self, messages: list[dict]) -> StreamResult:
        """Stream a chat completion, printing tokens to stdout as they arrive."""
        body: dict = {
            "messages": messages,
            "max_tokens": self.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self.model:
            body["model"] = self.model

        parts: list[str] = []
        t0 = time.monotonic()
        t_first: float | None = None
        completion_tokens = 0
        prompt_tokens = 0

        print()  # blank line before streamed output

        with self._client.stream(
            "POST", f"{self.base_url}/v1/chat/completions", json=body, timeout=None
        ) as resp:
            if resp.status_code != 200:
                resp.read()
                print(f"\n[ERROR {resp.status_code}] {resp.text[:200]}")
                return StreamResult("", 0.0, 0.0, 0, 0)

            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                # Usage summary chunk (stream_options.include_usage)
                if "usage" in chunk and not chunk.get("choices"):
                    usage = chunk["usage"]
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)
                    continue

                delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                token = delta.get("content") or delta.get("reasoning_content") or ""
                if token:
                    if t_first is None:
                        t_first = time.monotonic()
                    parts.append(token)
                    sys.stdout.write(token)
                    sys.stdout.flush()

        elapsed = time.monotonic() - t0
        ttft = (t_first - t0) if t_first is not None else elapsed

        if completion_tokens == 0:
            completion_tokens = len(parts)

        return StreamResult(
            text="".join(parts),
            ttft=ttft,
            elapsed=elapsed,
            completion_tokens=completion_tokens,
            prompt_tokens=prompt_tokens,
        )


# ── Terminal input ─────────────────────────────────────────────────────────────

class Terminal:
    @staticmethod
    @contextmanager
    def raw_mode():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            yield
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def read_input(self, prompt: str) -> str | None:
        """
        Read a line of input in raw mode.
        - Returns None on Ctrl-C / Ctrl-D (quit signal).
        - Returns "1"–"8" immediately (no Enter) when pressed on an empty line.
        - Otherwise echoes characters and returns the accumulated string on Enter.
        """
        sys.stdout.write(prompt)
        sys.stdout.flush()

        buf: list[str] = []

        with self.raw_mode():
            while True:
                ch = sys.stdin.read(1)
                code = ord(ch)

                if code in (3, 4):      # Ctrl-C, Ctrl-D
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return None

                elif code in (13, 10):  # Enter
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return "".join(buf)

                elif code == 127:       # Backspace
                    if buf:
                        buf.pop()
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()

                elif ch in "12345678" and not buf:
                    # Instant slot switch on empty line — no Enter needed
                    sys.stdout.write(ch + "\n")
                    sys.stdout.flush()
                    return ch

                elif 32 <= code < 127:  # printable ASCII
                    buf.append(ch)
                    sys.stdout.write(ch)
                    sys.stdout.flush()


# ── Slot tester REPL ───────────────────────────────────────────────────────────

class SlotTester:
    def __init__(self, chats: list[Chat], client: CompletionClient, terminal: Terminal) -> None:
        self.chats = chats
        self.client = client
        self.terminal = terminal
        self.active = 0  # 0-based index

    @property
    def current(self) -> Chat:
        return self.chats[self.active]

    def run(self) -> None:
        self._print_header()
        while True:
            line = self.terminal.read_input(self._prompt())
            if line is None:
                print("\nBye.")
                break

            line = line.strip()
            if not line:
                continue

            if self._try_slot_switch(line):
                continue
            if self._try_command(line):
                continue
            self._send_message(line)

    # ── private ────────────────────────────────────────────────────────────────

    def _prompt(self) -> str:
        c = self.current
        label = f"{c.turns} turn{'s' if c.turns != 1 else ''}"
        return f"\n[Slot {c.slot} | {label}]> "

    def _print_header(self) -> None:
        print("─" * 60)
        print("  1-8      switch slots (press on empty line, no Enter)")
        print("  ?        send a random topic question to the current slot")
        print("  /clear   reset current slot's history")
        print("  /history show current slot's message history")
        print("  /quit    exit  (Ctrl-C also works)")
        print("─" * 60)

    def _try_slot_switch(self, line: str) -> bool:
        if len(line) != 1 or line not in "12345678":
            return False
        new_idx = int(line) - 1
        if new_idx != self.active:
            self.active = new_idx
            c = self.current
            label = f"{c.turns} turn{'s' if c.turns != 1 else ''}"
            print(f"→ Slot {c.slot}: {c.topic} | {label}")
        return True

    def _try_command(self, line: str) -> bool:
        cmd = line.lower()
        if cmd in ("/quit", "/q"):
            print("Bye.")
            raise SystemExit(0)
        if cmd in ("/clear", "/reset"):
            self.current.clear()
            print(f"[Slot {self.current.slot}] History cleared.")
            return True
        if cmd == "/history":
            self._print_history()
            return True
        if cmd == "?":
            question = random.choice(_SLOT_QUESTIONS[self.current.slot])
            print(f"  ↳ {question}")
            self._send_message(question)
            return True
        return False

    def _print_history(self) -> None:
        if not self.current.history:
            print("[no history]")
            return
        for msg in self.current.history:
            snip = msg["content"][:120]
            ellipsis = "…" if len(msg["content"]) > 120 else ""
            print(f"  {msg['role'].upper()}: {snip}{ellipsis}")

    def _send_message(self, text: str) -> None:
        messages = self.current.build_messages(text)
        try:
            result = self.client.stream(messages)
        except httpx.ConnectError:
            print(f"\n[ERROR] Could not connect to {self.client.base_url}")
            return
        except Exception as exc:
            print(f"\n[ERROR] {exc}")
            return

        if result.text:
            print(f"\n\n{result.format_stats()}\n")
            self.current.append_turn(text, result.text)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interactive multi-chat KV-cache slot tester for llama-server"
    )
    parser.add_argument("server", help="server base URL, e.g. http://127.0.0.1:8080")
    parser.add_argument(
        "--context-size", "--cs",
        type=int, default=8192, metavar="TOKENS",
        help="approximate system-prompt size in tokens per slot (default: 8192)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int, default=512, metavar="N",
        help="max completion tokens per response (default: 512)",
    )
    parser.add_argument(
        "--model", "-m",
        default=None, metavar="ID",
        help="model ID to send in every request (omit to let the server choose)",
    )
    args = parser.parse_args()

    base_url = args.server.rstrip("/")
    if not base_url.startswith("http"):
        base_url = f"http://{base_url}"

    print(f"Building 8 system contexts (~{args.context_size} tokens each)...", end=" ", flush=True)
    chats = [Chat(slot=i, system_prompt=make_system_prompt(i, args.context_size)) for i in range(1, 9)]
    print("done\n")
    print(f"Server: {base_url}")
    if args.model:
        print(f"Model : {args.model}")
    print()

    with CompletionClient(base_url, args.max_tokens, args.model) as client:
        SlotTester(chats, client, Terminal()).run()


if __name__ == "__main__":
    main()
