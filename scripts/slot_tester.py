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


# ── Argument helpers ───────────────────────────────────────────────────────────

def parse_token_count(value: str) -> int:
    """Parse a token/context count with optional k/M/G suffix (binary: 1k=1024)."""
    s = value.strip()
    suffixes = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}
    if s and s[-1].lower() in suffixes:
        return int(float(s[:-1]) * suffixes[s[-1].lower()])
    return int(s)


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
        "Why does 0.999... equal exactly 1, and what's the cleanest proof?",
        "What's the key difference between proof by contradiction and proof by contrapositive?",
        "Why is 1 not considered a prime number?",
        "What's the core insight behind Euclid's proof that there are infinitely many primes?",
        "Why can't you trisect an arbitrary angle using only compass and straightedge?",
        "What's the difference between an axiom and a theorem?",
        "Why does multiplying two negative numbers give a positive result?",
        "What's a bijection and why does it matter when comparing infinite sets?",
        "Why is √2 irrational? Sketch the proof.",
        "What's the difference between necessary and sufficient conditions? Give a concrete example.",
        "Why does induction work, and what assumption does it actually rely on?",
        "What makes the pigeonhole principle surprising despite being obvious?",
        "Why does the sum of angles in a triangle differ between flat and spherical geometry?",
        "What's the difference between syntax and semantics in formal logic?",
        "Why is the empty set considered a subset of every set?",
        "What makes two logical statements equivalent rather than just both true?",
        "Why do mathematicians care about whether a proof is constructive?",
        "What's the difference between a tautology and a theorem?",
        "Why does casting out nines work as a quick arithmetic check?",
        "What's a counterexample, and why can a single one disprove a universal claim?",
    ],
    2: [  # world history, political philosophy, and geopolitics
        "What single decision do historians most often blame for prolonging WWI?",
        "Why did Gorbachev's reforms accelerate rather than prevent the Soviet collapse?",
        "What's the key difference between Hobbes' and Locke's views on the state of nature?",
        "Why did WWI reparations on Germany contribute to WWII rather than prevent it?",
        "What made the Marshall Plan unusual compared to typical post-war policy?",
        "Why did Napoleon's Russian campaign fail despite his earlier successes?",
        "What's the difference between soft power and hard power? Give a concrete example of each.",
        "What distinguishes a coup from a revolution?",
        "Why did the printing press shift political power in early modern Europe?",
        "What's the core tension in Rousseau's concept of the 'general will'?",
        "What distinguishes a federation from a confederation?",
        "Why did British decolonization accelerate after WWII compared to before it?",
        "What made the Cuban Missile Crisis resolve peacefully when other standoffs didn't?",
        "Why do historians debate whether the atomic bombings of Japan were militarily necessary?",
        "What's the difference between nationalism and patriotism?",
        "Why did the League of Nations fail where the UN has had more success?",
        "What's 'Thucydides' Trap' and which historical case best illustrates it?",
        "Why did Rome transition from republic to empire rather than strengthen its institutions?",
        "What's the difference between direct and representative democracy in practice?",
        "Why do authoritarian regimes often hold elections they control rather than abolish them?",
    ],
    3: [  # software engineering, distributed systems, and compilers
        "What's the difference between a race condition and a deadlock?",
        "Why does TCP use a three-way handshake rather than two?",
        "What's the key trade-off between a mutex and a spinlock?",
        "Why can't a Bloom filter tell you definitively that an element is in a set?",
        "What's the difference between memoization and caching?",
        "Why does tail recursion matter in a language without tail-call optimization?",
        "What's the key difference between stack and heap allocation?",
        "Why is O(n log n) the lower bound for comparison-based sorting?",
        "What does 'thread-safe' mean? Give a minimal concrete example of code that isn't.",
        "Why does copy-on-write make fork() efficient despite duplicating a process?",
        "What's the difference between a syntax error and a semantic error in a compiler?",
        "What's the key insight behind consistent hashing?",
        "Why do garbage collectors sometimes cause pause times despite running in the background?",
        "What's the difference between horizontal and vertical scaling?",
        "Why is a foreign key constraint more than just a naming convention?",
        "What's the difference between idempotency and determinism?",
        "Why does eventual consistency make distributed systems hard to reason about?",
        "What's the difference between parse time and runtime?",
        "Why can't the two-generals problem be solved over an unreliable channel?",
        "What makes an API 'RESTful' versus just HTTP-based?",
    ],
    4: [  # biology, genetics, and evolutionary ecology
        "Why do antibiotic-resistant bacteria evolve faster in hospitals than in the wild?",
        "What's the difference between a gene, an allele, and a locus?",
        "Why do liver and eye cells behave differently despite containing the same DNA?",
        "Why did sexual reproduction evolve when asexual reproduction is more efficient?",
        "Why does removing an apex predator sometimes cause prey populations to collapse rather than grow?",
        "What's the difference between a dominant and a recessive trait? Give a concrete example.",
        "Why does CRISPR cut at a specific location rather than randomly throughout the genome?",
        "What makes a species 'invasive' as opposed to simply non-native?",
        "Why do mitochondria have their own DNA separate from the cell nucleus?",
        "What's the difference between natural selection and genetic drift?",
        "Why can two unrelated species evolve similar structures like wings independently?",
        "What's the difference between mitosis and meiosis in terms of their purpose?",
        "Why does genetic diversity within a population matter for long-term survival?",
        "What makes a protein misfolded, and why is that dangerous?",
        "Why do some genes appear in nearly identical form across widely separated species?",
        "What's the difference between a mutation and a genetic variation?",
        "Why can't viruses survive long outside a host without special conditions?",
        "What's the key difference between a virus and a bacterium in terms of how they're treated medically?",
        "Why do humans have a blind spot in their vision, and what's the evolutionary explanation?",
        "What's the difference between an ecosystem and a biome?",
    ],
    5: [  # physics, quantum mechanics, and cosmology
        "Why does the double-slit experiment produce an interference pattern even when fired one photon at a time?",
        "Why does time pass more slowly near a massive object?",
        "Why can't anything escape a black hole if light has no mass to be pulled by gravity?",
        "What's the difference between nuclear fission and fusion in terms of where the energy comes from?",
        "Why does entropy always increase even though individual particle interactions are reversible?",
        "What's the key difference between dark matter and dark energy?",
        "Why does quantum tunneling allow alpha particles to escape atomic nuclei?",
        "What's the significance of the Planck constant?",
        "Why does adding mass to a neutron star eventually cause it to collapse into a black hole?",
        "What's the key insight of the equivalence principle in general relativity?",
        "Why is quantum mechanics described as probabilistic rather than just 'we don't know yet'?",
        "What physical reason makes absolute zero unattainable?",
        "What makes a laser different from an ordinary light source?",
        "Why does glass transmit visible light but block UV?",
        "What's the difference between speed and velocity, and why does it matter?",
        "Why did the Michelson-Morley experiment matter for special relativity?",
        "What's the difference between a fermion and a boson, and why does it matter?",
        "Why does the cosmic microwave background give us information about the early universe?",
        "What's the physical meaning of a wavefunction collapsing?",
        "Why does a spinning top resist falling over?",
    ],
    6: [  # literature, linguistics, and narrative craft
        "What makes an unreliable narrator different from one who is simply mistaken?",
        "Why does changing a story from first to third person change more than just the pronouns?",
        "What's the difference between a plot twist and a deus ex machina?",
        "Why do genre conventions constrain writers while also enabling them?",
        "What's the key difference between showing and telling, and when is telling actually better?",
        "What makes dialogue feel unnatural in a story?",
        "Why does sentence rhythm affect how readers experience tension?",
        "What's the difference between a motif and a symbol in literary analysis?",
        "Why does the opening sentence of a novel carry disproportionate weight?",
        "What makes satire different from simple criticism or mockery?",
        "Why does repetition in poetry create meaning rather than just redundancy?",
        "What's the difference between theme and moral in a story?",
        "Why do some languages lack a word for a concept that another language expresses easily?",
        "What makes foreshadowing effective versus heavy-handed?",
        "What's the difference between dialect and accent in written dialogue?",
        "Why does the choice of tense affect the reader's sense of distance from events?",
        "What's the difference between diegetic and non-diegetic elements in storytelling?",
        "What makes metaphor fundamentally different from simile beyond just omitting 'like'?",
        "Why do stories built around conflict feel more compelling than those built around description?",
        "What's the difference between irony and sarcasm?",
    ],
    7: [  # macroeconomics, market dynamics, and monetary theory
        "Why does printing more money cause inflation rather than making everyone wealthier?",
        "Why do central banks target 2% inflation rather than zero?",
        "What's the key difference between a stock and a bond as investments?",
        "Why does comparative advantage lead to trade even when one country is better at everything?",
        "What makes a currency peg difficult to maintain during a financial crisis?",
        "Why does raising interest rates slow inflation?",
        "Why do bank runs happen and how does deposit insurance prevent them?",
        "What's the difference between cost-push and demand-pull inflation?",
        "Why does purchasing power parity not hold perfectly in practice?",
        "What's moral hazard? Give a concrete example from the 2008 financial crisis.",
        "Why does GDP growth not always correlate with rising living standards?",
        "What's the difference between nominal and real interest rates?",
        "Why do monopolies tend to produce less and charge more than competitive markets?",
        "What's the difference between a recession and a depression?",
        "Why can a government deficit sometimes be beneficial rather than harmful?",
        "What's the difference between the money supply measures M1 and M2?",
        "Why does the velocity of money matter for inflation?",
        "What makes a tax regressive versus progressive?",
        "What's the key insight of the prisoner's dilemma for market competition?",
        "Why does a trade surplus not automatically mean an economy is doing well?",
    ],
    8: [  # philosophy of mind, ethics, and epistemology
        "Why does the trolley problem produce different intuitions when you push someone versus pull a lever?",
        "What's the key difference between Kant's categorical imperative and a simple golden rule?",
        "What's the difference between justified true belief and knowledge, per the Gettier problem?",
        "Why does Descartes conclude 'I think therefore I am' rather than 'I breathe therefore I am'?",
        "Why does free will seem incompatible with determinism?",
        "What's the difference between moral relativism and moral subjectivism?",
        "Why does utilitarianism struggle with the repugnant conclusion?",
        "What's the difference between phenomenal and access consciousness?",
        "Why is the problem of induction hard to resolve without circular reasoning?",
        "What's the difference between an argument being valid and being sound?",
        "What's the key difference between strong and weak AI in philosophical terms?",
        "Why does the Chinese Room argument challenge machine consciousness?",
        "What's the difference between personal identity and psychological continuity?",
        "Why does the concept of consciousness create difficulties for physicalism?",
        "What's the key objection to ethical egoism as a moral theory?",
        "Why does Rawls argue rational people behind a veil of ignorance would choose equal distribution?",
        "What makes a thought experiment useful in philosophy when it describes an impossible scenario?",
        "What's the difference between moral realism and moral anti-realism?",
        "Why does the existence of evil present a specific problem for the argument from design?",
        "What's the difference between an empirical claim and a normative claim?",
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
    gen_elapsed: float
    completion_tokens: int
    prompt_tokens: int

    def format_stats(self) -> str:
        tps = self.completion_tokens / self.gen_elapsed if self.gen_elapsed > 0 else 0.0
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
        t_last: float | None = None
        completion_tokens = 0
        prompt_tokens = 0

        print()  # blank line before streamed output

        with self._client.stream(
            "POST", f"{self.base_url}/v1/chat/completions", json=body, timeout=None
        ) as resp:
            if resp.status_code != 200:
                resp.read()
                print(f"\n[ERROR {resp.status_code}] {resp.text[:200]}")
                return StreamResult("", 0.0, 0.0, 0.0, 0, 0)

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
                    t_last = time.monotonic()
                    if t_first is None:
                        t_first = t_last
                    parts.append(token)
                    sys.stdout.write(token)
                    sys.stdout.flush()

        elapsed = time.monotonic() - t0
        ttft = (t_first - t0) if t_first is not None else elapsed
        gen_elapsed = (t_last - t_first) if (t_first is not None and t_last is not None and t_last > t_first) else 0.0

        if completion_tokens == 0:
            completion_tokens = len(parts)

        return StreamResult(
            text="".join(parts),
            ttft=ttft,
            elapsed=elapsed,
            gen_elapsed=gen_elapsed,
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
        "--context-size", "--ctx",
        type=parse_token_count, default=8192, metavar="TOKENS",
        help="approximate system-prompt size in tokens per slot (default: 8192)",
    )
    parser.add_argument(
        "--max-tokens",
        type=parse_token_count, default=2048, metavar="N",
        help="max completion tokens per response (default: 2048)",
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
