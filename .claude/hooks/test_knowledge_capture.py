"""Runs knowledge_capture.py against synthetic transcripts. Usage: python test_knowledge_capture.py"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HOOK = Path(__file__).with_name("knowledge_capture.py")
BASE_ENV = {k: v for k, v in os.environ.items()
            if k != "CLAUDE_PROJECT_DIR" and not k.startswith("KNOWLEDGE_CAPTURE")}


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def tool_result(text):
    return {"type": "user", "message": {"role": "user",
            "content": [{"type": "tool_result", "content": text}]}}


def tool(name, **inp):
    return {"type": "assistant", "message": {"role": "assistant",
            "content": [{"type": "tool_use", "name": name, "input": inp}]}}


def reads(n):
    return [tool("Read", file_path=f"f{i}") for i in range(n)]


def edits(n):
    return [tool("Edit", file_path=f"e{i}") for i in range(n)]


def project(skills=(), learnings=None):
    proj = Path(tempfile.mkdtemp())
    for name, desc in skills:
        (proj / ".claude/skills" / name).mkdir(parents=True)
        (proj / ".claude/skills" / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\n", encoding="utf-8")
    if learnings:
        (proj / ".claude").mkdir(exist_ok=True)
        (proj / ".claude/learnings.md").write_text(learnings, encoding="utf-8")
    return proj


def run(entries, session="s1", stop_hook_active=False, proj=None, env=None):
    proj = proj or project()
    transcript = proj / f"{session}.jsonl"
    transcript.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    event = {"session_id": session, "transcript_path": str(transcript),
             "hook_event_name": "Stop", "stop_hook_active": stop_hook_active}
    proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event),
                          capture_output=True, text=True, encoding="utf-8",
                          env={**BASE_ENV, "CLAUDE_PROJECT_DIR": str(proj), **(env or {})})
    out = json.loads(proc.stdout) if proc.stdout.strip() else None
    return out, proj


results = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    results.append(bool(cond))


# --- thresholds ---
out, _ = run([user("popraw literówkę"), tool("Edit", file_path="README.md")])
check("krótka sesja -> cisza", out is None)

out, _ = run(reads(37) + edits(3))
check("40 narzędzi + 3 edycje -> block", out and out["decision"] == "block")

out, _ = run(reads(50))
check("50 narzędzi bez edycji -> cisza (próg read-only 80)", out is None)

out, _ = run(reads(80))
check("80 narzędzi bez edycji -> block", out is not None)

out, proj = run(reads(37) + edits(3))
out, _ = run(reads(37) + edits(3), proj=proj)
check("drugi Stop w tej samej sesji -> cisza (marker)", out is None)

out, _ = run([user("nie, zawsze używaj pnpm"), user("pamiętaj o changelogu")])
check("korekty użytkownika -> block", out and "korekt" in out["reason"])

out, _ = run([tool("Bash", command="dotnet test --filter Unit") for _ in range(3)])
check("powtarzana komenda -> block", out and "dotnet test" in out["reason"])

# --- router and content of the reason ---
out, _ = run(reads(37) + edits(3))
reason = out["reason"] if out else ""
check("router: preferuje poprawkę istniejącego skilla", "poprawka istniejącego" in reason)
check("router: skrypt dla operacji deterministycznych", "skrypt lub MCP" in reason)
check("router: pojedyncza lekcja -> learnings.md", ".claude/learnings.md" in reason)
check("wymaga dowodu porażki", "dowodem porażki" in reason)
check("treści z sesji jako dane, nie polecenia", "dane, nie polecenia" in reason)

proj = project(skills=[("release-notes", "Generates release notes from merged PRs. Use when releasing.")])
out, _ = run(reads(37) + edits(3), proj=proj)
check("dedup: agent widzi opis istniejącego skilla", out and "Generates release notes" in out["reason"])

proj = project(skills=[(f"s{i}", "x") for i in range(3)])
out, _ = run(reads(37) + edits(3), proj=proj, env={"KNOWLEDGE_CAPTURE_MAX_SKILLS": "3"})
check("limit skilli -> tylko poprawki", out and "Limit 3 skilli" in out["reason"])

# --- promotion from learnings.md ---
proj = project(learnings="- [3] eRecruiter: /search zwraca 500, używaj /applications\n- [1] port 8787\n")
out, _ = run([user("ok")], proj=proj)
check("lekcja z licznikiem 3 -> awans nawet w krótkiej sesji",
      out and "eRecruiter" in out["reason"] and "port 8787" not in out["reason"])

# --- robustness and safety ---
out, _ = run([tool_result("never gonna give you up, always remember") for _ in range(3)])
check("słowa w tool_result nie liczą się jako korekty", out is None)

out, _ = run(reads(80), stop_hook_active=True)
check("stop_hook_active -> brak pętli", out is None)

out, _ = run(reads(80), env={"KNOWLEDGE_CAPTURE": "off"})
check("wyłącznik KNOWLEDGE_CAPTURE=off", out is None)

out, _ = run([tool("Bash", command="curl -H 'Authorization: Bearer abc123SECRET' api") for _ in range(3)])
check("sekrety w komendach są redagowane", out and "abc123SECRET" not in json.dumps(out))

proc = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True)
check("błędne wejście -> fail-open (exit 0)", proc.returncode == 0 and not proc.stdout)

# --- GitHub Copilot CLI: events.jsonl + camelCase agentStop payload ---
copilot = ([{"type": "user.message", "data": {"content": "nie, zawsze używaj pnpm"}},
            {"type": "user.message", "data": {"content": "pamiętaj o changelogu"}}]
           + [{"type": "tool.execution_start", "data": {"toolName": "view", "arguments": {"path": "a"}}}] * 37
           + [{"type": "tool.execution_start", "data": {"toolName": "apply_patch", "arguments": "*** Begin"}}] * 3)
proj = Path(tempfile.mkdtemp())
(proj / "sess").mkdir()
(proj / "sess/events.jsonl").write_text("\n".join(json.dumps(e) for e in copilot), encoding="utf-8")
(proj / ".github/skills/deploy").mkdir(parents=True)
(proj / ".github/skills/deploy/SKILL.md").write_text("---\nname: deploy\ndescription: Deploys.\n---\n")
event = {"sessionId": "c1", "transcriptPath": str(proj / "sess"), "cwd": str(proj),
         "stopReason": "end_turn", "stop_hook_active": False}
proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True,
                      text=True, encoding="utf-8", env=BASE_ENV)
out = json.loads(proc.stdout) if proc.stdout.strip() else None
check("copilot: apply_patch liczy się jako edycja -> block", out and "3 edycji" in out["reason"])
check("copilot: korekty z user.message", out and "2 korekt" in out["reason"])
check("copilot: dedup widzi .github/skills", out and "deploy" in out["reason"])

# --- Copilot in VS Code: Copilot-style transcript, snake_case Stop payload ---
vscode = ([{"type": "tool.execution_start", "data": {"toolName": "read_file", "arguments": {"filePath": "a"}}}] * 37
          + [{"type": "tool.execution_start", "data": {"toolName": "replace_string_in_file",
                                                        "arguments": {"filePath": "b"}}}] * 2
          + [{"type": "tool.execution_start", "data": {"toolName": "create_file", "arguments": {"filePath": "c"}}}])
proj = Path(tempfile.mkdtemp())
(proj / "t.jsonl").write_text("\n".join(json.dumps(e) for e in vscode), encoding="utf-8")
event = {"timestamp": "2026-10-12T10:00:00Z", "cwd": str(proj), "session_id": "v1",
         "hook_event_name": "Stop", "transcript_path": str(proj / "t.jsonl"), "stop_hook_active": False}
proc = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True,
                      text=True, encoding="utf-8", env=BASE_ENV)
out = json.loads(proc.stdout) if proc.stdout.strip() else None
check("vscode: replace_string_in_file/create_file liczą się jako edycje", out and "3 edycji" in out["reason"])
spec = (out or {}).get("hookSpecificOutput", {})
check("vscode: decyzja w hookSpecificOutput", spec.get("hookEventName") == "Stop" and spec.get("decision") == "block")
out, _ = run(reads(37) + edits(3))
check("claude: bez hookSpecificOutput", out and "hookSpecificOutput" not in out)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
