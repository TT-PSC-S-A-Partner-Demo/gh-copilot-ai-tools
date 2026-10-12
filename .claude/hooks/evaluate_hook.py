"""Replays knowledge_capture.py heuristics over your local transcripts (read-only).

Shows how often the hook would fire for Claude Code, Copilot CLI and Copilot in VS Code sessions,
which signals drive it, and how long the analysis takes. Use it to tune thresholds.

Usage: python .claude/hooks/evaluate_hook.py
"""
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import knowledge_capture as kc  # noqa: E402

SOURCES = {
    "claude": "~/.claude/projects/*/*.jsonl",
    "copilot": "~/.copilot/session-state/*/events.jsonl",
    "vscode": os.path.join(os.environ.get("APPDATA") or "~/.config",
                           "Code/User/workspaceStorage/*/GitHub.copilot-chat/transcripts/*.jsonl"),
}


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else 0


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    for name, pattern in SOURCES.items():
        rows = []
        for path in glob.glob(os.path.expanduser(pattern)):
            start = time.perf_counter()
            tools, edits, corrections, repeated = kc.analyze(path)
            rows.append((tools, edits, corrections, len(repeated), (time.perf_counter() - start) * 1000))
        if not rows:
            continue
        process = sum((t >= kc.MIN_TOOL_CALLS and e >= kc.MIN_FILE_EDITS) or t >= kc.MIN_TOOL_CALLS_READONLY
                      for t, e, *_ in rows)
        corr = sum(c >= kc.MIN_CORRECTIONS for _, _, c, _, _ in rows)
        rep = sum(r > 0 for *_, r, _ in rows)
        fired = sum(((t >= kc.MIN_TOOL_CALLS and e >= kc.MIN_FILE_EDITS) or t >= kc.MIN_TOOL_CALLS_READONLY)
                    or c >= kc.MIN_CORRECTIONS or r > 0 for t, e, c, r, _ in rows)
        print(f"== {name}: {len(rows)} sesji")
        print(f"   narzędzia: mediana {percentile([r[0] for r in rows], .5)}, p75 {percentile([r[0] for r in rows], .75)}")
        print(f"   sygnały:   proces {process}, korekty {corr}, powtórzenia {rep}")
        print(f"   odpaliłby: {fired}/{len(rows)} ({100 * fired / len(rows):.0f}%)")
        print(f"   czas analizy: max {max(r[4] for r in rows):.0f} ms")


if __name__ == "__main__":
    main()
