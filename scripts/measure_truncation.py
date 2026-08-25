#!/usr/bin/env python
"""How much of the corpus does the embedding model never see? — [OQ-18](docs/OPEN_QUESTIONS.md#oq-18)

    uv run python scripts/measure_truncation.py
    uv run python scripts/measure_truncation.py --json out.json

OQ-18 names two levers for the 61%-against-an-80%-gate recall problem and says which to
measure first, and why:

> **Measure the second first** — it is a property of the corpus that can be counted
> without building anything, and it would change what the first experiment means.

That second lever is the 512-token truncation. `rag/chunking.py` splits in **characters**
(`MAX_CHARS = 1800`, "~500 tokens") because character boundaries are model-independent and
that was the right call while the model was still being chosen. The model is now fixed
(`bge-m3`, OQ-02), and ~3.6 chars/token is an English average: identifier-dense code and
Russian text both tokenize denser, so a minority of chunks exceed the model's 512-token
window and are **silently truncated** — the model embeds a prefix and the tail is invisible
to retrieval for ever.

This script counts exactly that. It needs the tokenizer and **not** the ONNX model, so it
costs seconds rather than the tens of minutes `eval_embeddings.py` costs: tokenizing is
the whole measurement.

Three numbers come out, and they answer different questions:

1. **What fraction of chunks truncate**, and by how much. If it is ~0%, OQ-18's second
   lever is dead and query translation is the only one left.
2. **What fraction of *tokens* are lost.** A 1% truncation rate that removes 60% of each
   affected chunk is a different problem from a 20% rate that removes 2%.
3. **Whether the fixtures' expected files are affected.** Seven of the 25 Russian cases
   never enter the candidate list at all. If their source files are the ones truncating,
   this is a chunking bug being read as a retrieval one — which is much cheaper to fix.

Reuses `eval_embeddings.py`'s corpus walk and chunker rather than re-implementing them: a
second copy of the chunker would make this measure something the shipped index does not do.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests/fixtures/retrieval/cases.yaml"
DEFAULT_MODELS = Path("D:/ORACLE/models/embeddings")

#: bge-m3's window (OQ-02). The number the whole measurement is about.
LIMIT = 512

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

#: The three answers this measurement can produce, written out so the conclusion is a
#: property of the data rather than of whoever reads the numbers afterwards.
NEGLIGIBLE = """  Truncation is negligible corpus-wide. The second lever is dead, and query
  translation is the remaining hypothesis."""

MARGINAL = """  Truncation is real corpus-wide and MARGINAL on the fixtures. No expected
  source loses a meaningful share of itself, so truncation cannot be what keeps the
  failing Russian cases out of the candidate list. The second lever is measured and it
  is NOT the cause: query translation is the hypothesis left, and fixing chunking is a
  separate, worthwhile corpus repair."""

CAUSAL = """  Truncation reaches the files the failing fixtures point at and takes a
  meaningful share of them. A token-aware splitter is the cheap experiment and must run
  before query translation: translating a query cannot retrieve text the index never
  embedded."""


def _load_eval() -> Any:
    """Import the sibling script as a module. It is a script rather than a package
    member on purpose (it needs models on disk and costs CPU), and duplicating its
    chunker here would make this measure something the index does not do."""
    spec = importlib.util.spec_from_file_location(
        "eval_embeddings", Path(__file__).parent / "eval_embeddings.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("scripts/eval_embeddings.py could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_embeddings"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="bge-m3", help="tokenizer to measure against")
    ap.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS)
    ap.add_argument("--limit", type=int, default=LIMIT)
    ap.add_argument("--json", type=Path, help="write the full result here")
    args = ap.parse_args()

    # `fetch_embedding_models.py` lands the tokenizer beside the ONNX graph; older
    # layouts put it at the model root. Try both rather than making the caller care.
    candidates = [
        args.models_dir / args.model / "onnx" / "tokenizer.json",
        args.models_dir / args.model / "tokenizer.json",
    ]
    tokenizer_path = next((p for p in candidates if p.exists()), None)
    if tokenizer_path is None:
        print(f"{RED}tokenizer not found; looked in:{RESET}")
        for p in candidates:
            print(f"  {p}")
        print("fetch it with scripts/fetch_embedding_models.py")
        return 2

    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(tokenizer_path))
    tok.no_truncation()
    tok.no_padding()

    ev = _load_eval()
    print(f"{DIM}walking the corpus…{RESET}")
    docs = ev.load_corpus(verbose=True)
    chunks = [c for d in docs for c in ev.chunk_doc(d)]
    print(f"  {len(docs)} documents, {len(chunks)} chunks")

    print(f"{DIM}tokenizing with {args.model}…{RESET}")
    lengths = [len(tok.encode(c.text).ids) for c in chunks]

    over = [n for n in lengths if n > args.limit]
    total_tokens = sum(lengths)
    lost_tokens = sum(n - args.limit for n in over)
    by_kind: Counter[str] = Counter()
    over_by_kind: Counter[str] = Counter()
    for chunk, n in zip(chunks, lengths, strict=True):
        by_kind[chunk.doc.kind] += 1
        if n > args.limit:
            over_by_kind[chunk.doc.kind] += 1

    # Per-file truncation, so the fixture question can be answered.
    worst: dict[str, dict[str, Any]] = {}
    for chunk, n in zip(chunks, lengths, strict=True):
        row = worst.setdefault(
            chunk.doc.path, {"chunks": 0, "over": 0, "max": 0, "tokens": 0, "lost": 0}
        )
        row["chunks"] += 1
        row["max"] = max(row["max"], n)
        row["tokens"] += n
        if n > args.limit:
            row["over"] += 1
            row["lost"] += n - args.limit

    rate = len(over) / len(chunks) if chunks else 0.0
    chars = [len(c.text) for c in chunks]
    over_chars = [n for n in chars if n > ev.MAX_CHARS]
    print()
    print(
        f"  chunks over MAX_CHARS ({ev.MAX_CHARS}): {len(over_chars)}/{len(chunks)} "
        f"({len(over_chars) / max(len(chunks), 1):.1%}), longest {max(chars, default=0)} chars"
    )
    print(f"  chunks over {args.limit} tokens: {len(over)}/{len(chunks)} ({rate:.1%})")
    if over:
        print(
            f"  of those: median {statistics.median(over):.0f} tokens, "
            f"max {max(over)}, mean overshoot {statistics.mean(over) - args.limit:.0f}"
        )
    print(
        f"  tokens never embedded: {lost_tokens}/{total_tokens} ({lost_tokens / max(total_tokens, 1):.2%})"
    )
    print("  by kind: " + ", ".join(f"{k} {over_by_kind[k]}/{by_kind[k]}" for k in sorted(by_kind)))

    # -- the fixtures, which is what OQ-18 actually needs to know ----------------
    import yaml

    cases = yaml.safe_load(FIXTURES.read_text(encoding="utf-8"))["cases"]
    russian = [c for c in cases if c.get("lang") == "ru"]
    print()
    print(f"  {len(russian)} Russian fixtures; their expected sources:")
    affected = 0
    fixture_rows: list[dict[str, Any]] = []

    def resolve(expected: str) -> dict[str, Any] | None:
        """Find the corpus path a fixture means, using `eval_embeddings.hit`'s own rule
        (`e in f or f.endswith(e)`) rather than an exact key.

        The first version of this script used a dict lookup and reported seven Russian
        fixtures as NOT IN THE CORPUS — which is a *different claim* from the one OQ-18
        makes about them, and it was wrong: the notes collection keys documents relative
        to its root, so the vault directory is a prefix the fixture does not carry. Two
        different matching rules for "which file is this" is how a measurement ends up
        describing the measurer."""
        exact = worst.get(expected)
        if exact is not None:
            return exact
        matches = [
            row for path, row in worst.items() if expected in path or path.endswith(expected)
        ]
        if not matches:
            return None
        merged = {"chunks": 0, "over": 0, "max": 0, "tokens": 0, "lost": 0}
        for row in matches:
            for key in ("chunks", "over", "tokens", "lost"):
                merged[key] += row[key]
            merged["max"] = max(merged["max"], row["max"])
        return merged

    for case in russian:
        for expected in case.get("expect_any", []):
            row = resolve(expected)
            if row is None:
                print(f"    {YELLOW}{case['id']}: {expected} — NOT IN THE CORPUS{RESET}")
                fixture_rows.append({"case": case["id"], "path": expected, "in_corpus": False})
                continue
            flag = RED if row["over"] else GREEN
            if row["over"]:
                affected += 1
            share = row["lost"] / max(row["tokens"], 1)
            print(
                f"    {flag}{case['id']}: {expected} — "
                f"{row['over']}/{row['chunks']} chunks over, longest {row['max']}, "
                f"{share:.0%} of its tokens never embedded{RESET}"
            )
            fixture_rows.append({"case": case["id"], "path": expected, "in_corpus": True, **row})
    # How *badly* a fixture's file is affected matters more than whether it is at all,
    # and the honest denominator is tokens rather than chunks: "1 of 2 chunks over" is
    # 50% of a two-chunk Dockerfile and tells you nothing, while "12% of this file's
    # tokens are never embedded" is the quantity that could actually hide an answer.
    worst_share = max(
        (r["lost"] / max(r["tokens"], 1) for r in fixture_rows if r.get("tokens")), default=0.0
    )
    print()
    print(
        f"  {affected} of the Russian fixtures' expected files contain at least one "
        f"truncated chunk; the worst loses {worst_share:.0%} of one file's tokens."
    )
    print()
    print(f"{DIM}What this means for OQ-18:{RESET}")
    if rate < 0.02:
        print(NEGLIGIBLE)
    elif worst_share < 0.15:
        print(MARGINAL)
    else:
        print(CAUSAL)

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "model": args.model,
                    "limit": args.limit,
                    "documents": len(docs),
                    "chunks": len(chunks),
                    "over_limit": len(over),
                    "rate": rate,
                    "tokens_total": total_tokens,
                    "tokens_lost": lost_tokens,
                    "by_kind": {
                        k: {"chunks": by_kind[k], "over": over_by_kind[k]} for k in by_kind
                    },
                    "russian_fixtures": fixture_rows,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"{DIM}  written to {args.json}{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
