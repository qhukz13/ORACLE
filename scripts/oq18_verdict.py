"""ADR-0027's verdict, read off the OQ-18 result rather than argued.

[ADR-0027](../docs/DECISIONS.md) weighted the lexical list down inside RRF. It was accepted on a
**ceiling estimate**: `rrf_w2` (weighted but ungated) scored 68% against `rrf`'s 61%, and the
shipped path is neither of those — it is gated *and* weighted. `eval_embeddings.py` measures all
four cells in one run, so the change can be checked against the arm it actually became:

```
              ungated      gated
unweighted    rrf          gated      <- before ADR-0027
weighted      rrf_w2       gated_w2   <- what ships
```

**The decisive comparison is `gated_w2` vs `gated`, within one run.** Both see the same corpus, the
same fixture set and the same denominator, so nothing about the machine or the week can explain a
difference. A cross-run comparison against the previous pass's 61.1% is printed too, as a sanity
check, but it is the weaker evidence: that pass scored 38 fixtures where this one scores 36, two
having been deleted in another repository.

**The pre-committed decision, recorded before the run finished:** if `gated_w2` does not beat
`gated`, revert ADR-0027 — the rollback is one line, `lexical_weight=1.0` in `rag/retrieval.py`.

Run: `uv run python scripts/oq18_verdict.py`
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "logs" / "measurements" / "oq18-translated.json"

#: The previous complete pass, rescored over the 36 fixtures still reachable. Computed from that
#: run's per-arm miss lists before this run started, so the baseline could not be chosen after
#: seeing the answer.
PREVIOUS_N36 = {"dense": 0.611, "rrf_w2": 0.694, "gated": 0.611}

#: Phase 5's criterion. Neither arm is expected to reach it; ADR-0027 never claimed it would.
GATE = 0.80


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default=str(DEFAULT))
    args = ap.parse_args()

    path = Path(args.result)
    if not path.exists():
        print(f"no result at {path} — the run has not finished")
        return 1

    payload = json.loads(path.read_text(encoding="utf-8"))
    corpus = payload.get("corpus", {})
    print(
        f"corpus   {corpus.get('docs')} docs, {corpus.get('chunks')} chunks, "
        f"{corpus.get('semantic')} semantic"
    )

    for result in payload.get("results", []):
        fusion = result.get("fusion", {})
        name = result.get("name", "?")
        print(f"\n{name}")
        print("               ungated     gated")
        print(f"  unweighted  {fusion.get('rrf', 0):>7.1%}   {fusion.get('gated', 0):>7.1%}")
        print(f"  weighted    {fusion.get('rrf_w2', 0):>7.1%}   {fusion.get('gated_w2', 0):>7.1%}")
        print(f"  (dense-only {fusion.get('dense', 0):>7.1%})")

        gated, gated_w2 = fusion.get("gated", 0.0), fusion.get("gated_w2", 0.0)
        delta = gated_w2 - gated
        print(
            f"\n  ADR-0027's arm:  gated_w2 {gated_w2:.1%}  vs  gated {gated:.1%}   "
            f"delta {delta:+.1%}"
        )

        if delta > 0:
            print("  VERDICT: the weighting helps the shipped path. ADR-0027 stands.")
        elif delta == 0:
            # The outcome the ADR pre-committed to acting on, and the easiest to rationalise
            # away, so it is spelled out rather than left to the reader.
            print("  VERDICT: NO MOVEMENT. Revert ADR-0027 — `lexical_weight = 1.0`.")
        else:
            print("  VERDICT: the weighting HURTS the shipped path. Revert ADR-0027.")

        print(
            f"\n  gate (recall@5 >= {GATE:.0%}): "
            f"{'MET' if max(fusion.values(), default=0) >= GATE else 'still missed'}"
        )

        print("\n  cross-run, weaker evidence (previous pass rescored to n=36):")
        for arm, was in PREVIOUS_N36.items():
            now = fusion.get(arm)
            if now is None:
                continue
            print(f"    {arm:<8} was {was:.1%}  now {now:.1%}   {now - was:+.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
