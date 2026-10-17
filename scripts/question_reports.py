"""
question_reports.py
===================
Questions the Ask page or the assistant couldn't answer, or answered the wrong way, reported by the
user so the developers can add or fix them. A report is kept on this computer (one JSON object per
line in question-reports.jsonl, in the Windows app's data folder) and can be sent as a GitHub issue:
issue_url() opens GitHub's new-issue page filled in, so the user sees and can edit it before anything
is sent, and nothing is sent without them pressing GitHub's own button. A report holds the question,
what was answered, where it was asked and the app's version: never the stats themselves.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

REPORTS_FILE = "question-reports.jsonl"
ISSUE_LABEL = "ask-report"
_MAX_TEXT = 1000  # characters kept of each field
_MAX_REPORTS = 500  # the oldest go once there are more


def new_report(question: str, *, answer: str = "", understood: str = "", where: str = "Ask", page: str = "",
               comment: str = "", version: str = "") -> dict[str, Any]:
    """A report of `question`: `answer` is what it said, `where` "Ask" or "Assistant", `comment` what the
    user expected."""
    return {"id": uuid.uuid4().hex[:12], "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "question": question.strip()[:_MAX_TEXT], "answer": answer.strip()[:_MAX_TEXT],
            "understood": understood.strip()[:_MAX_TEXT], "where": where, "page": page,
            "comment": comment.strip()[:_MAX_TEXT], "version": version}


def load_reports(folder: Path | None) -> list[dict[str, Any]]:
    """The saved reports, newest first ([] if none, or with nowhere to keep them)."""
    if folder is None:
        return []
    try:
        lines = (Path(folder) / REPORTS_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    reports = []
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue  # a damaged line costs only itself
        if isinstance(value, dict) and value.get("question"):
            reports.append(value)
    return reports[::-1]


def _write(folder: Path, reports: list[dict[str, Any]]) -> None:
    """Replace the file with `reports` (newest first) in one step, so it's never half written."""
    folder.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=folder, delete=False, suffix=".tmp") as file:
            temporary = file.name
            for report in reversed(reports[:_MAX_REPORTS]):
                file.write(json.dumps(report, ensure_ascii=False) + "\n")
        os.replace(temporary, folder / REPORTS_FILE)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def save_report(folder: Path, report: dict[str, Any]) -> None:
    """Keep a report (the same question reported again replaces its older report)."""
    folder = Path(folder)
    same = report["question"].casefold()
    _write(folder, [report] + [r for r in load_reports(folder) if r["question"].casefold() != same])


def delete_report(folder: Path, report_id: str) -> None:
    folder = Path(folder)
    _write(folder, [r for r in load_reports(folder) if r.get("id") != report_id])


def issue_title(report: dict[str, Any]) -> str:
    question = " ".join(report["question"].split())
    return f"Ask couldn't answer: {question[:80]}{'…' if len(question) > 80 else ''}"


def issue_body(report: dict[str, Any]) -> str:
    lines = ["**Question**", "", f"> {report['question']}", ""]
    if report.get("comment"):
        lines += ["**What I expected**", "", report["comment"], ""]
    if report.get("answer"):
        lines += ["**What it answered**", "", f"> {report['answer']}", ""]
    if report.get("understood"):
        lines += [f"It looked up: {report['understood']}", ""]
    where = report.get("where", "Ask") + (f" (on {report['page']})" if report.get("page") else "")
    lines += ["---", f"Asked in: {where} · R6 Match Stats {report.get('version') or ''}".rstrip(),
              "_Sent from the app's Report button. No stats are included._"]
    return "\n".join(lines)


def issue_url(report: dict[str, Any], repo: str) -> str:
    """GitHub's new-issue page for `repo`, filled in with the report (kept short enough for a URL)."""
    body = issue_body(report)
    if len(body) > 4000:
        body = body[:4000] + "…"
    return f"https://github.com/{repo}/issues/new?" + urlencode({"title": issue_title(report), "body": body,
                                                                  "labels": ISSUE_LABEL})


if __name__ == "__main__":  # for the developers: python scripts/question_reports.py [folder]
    import sys

    from app_info import desktop_data_dir

    found = load_reports(Path(sys.argv[1]) if len(sys.argv) > 1 else desktop_data_dir())
    for r in found:
        print(f"{r['time']}  [{r.get('where', '')}] {r['question']}" + (f"\n    expected: {r['comment']}"
                                                                      if r.get("comment") else ""))
    print(f"{len(found)} report{'s' if len(found) != 1 else ''}")
