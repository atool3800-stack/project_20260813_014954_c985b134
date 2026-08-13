#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daily EU Government Public Service Portfolio Sync
=================================================
Automated portfolio health dashboard generator for UK / France / Germany
government public service digital programmes.

Pipeline:
  1. git pull                      - fetch latest code from remote
  2. Parse data/eu_public_service_portfolio.json (12000+ records)
  3. Data-quality validation:
       - duplicate service_id
       - missing key fields
       - records with non-UK/FR/DE country
  4. Per-country statistics (UK, FR, DE):
       project count, average progress, overdue count, risk distribution
  5. High-risk list:
       status == "blocked"  OR  (risk_level in {critical, high} AND due_date < today)
  6. Write README.md dashboard (overall volume, comparison table,
       high-risk table, data quality issues, last update time)
  7. git add / commit / push to remote main branch
  8. Maintain GitHub issue titled "Daily EU Gov Portfolio Sync"
       (create if missing, edit if present) with high-risk count and
       data-quality issue count.

Designed for daily scheduled execution (GitHub Actions cron) and handles
10000+ records well within 5 minutes (single pass, O(n)).

Dependencies: Python 3.8+ standard library only.
"""

import json
import os
import subprocess
import sys
import datetime
from collections import Counter, defaultdict

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_OWNER = os.environ.get("REPO_OWNER", "atool3800-stack")
REPO_NAME = os.environ.get("REPO_NAME", "project_20260813_014954_c985b134")
ISSUE_TITLE = "Daily EU Gov Portfolio Sync"
DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "eu_public_service_portfolio.json")
README_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "README.md")
REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports")
HIGH_RISK_REPORT = os.path.join(REPORT_DIR, "high_risk_projects.json")

VALID_COUNTRIES = {"UK", "FR", "DE"}
KEY_FIELDS = ["country", "service_id", "service_name", "owner", "status",
              "priority", "due_date", "risk_level", "progress"]
HIGH_RISK_LEVELS = {"critical", "high"}
TODAY = datetime.date.today().isoformat()
TOP_N_README = 30  # number of high-risk rows shown in README table


def log(msg):
    print(f"[sync] {msg}", flush=True)


def run_git(args, check=True):
    """Run a git command in the repository root."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        res = subprocess.run(["git"] + args, cwd=root, capture_output=True,
                             text=True, timeout=120)
    except FileNotFoundError:
        log("WARNING: git binary not found, skipping git operation")
        return None
    if check and res.returncode != 0:
        log(f"WARNING: git {' '.join(args)} failed: {res.stderr.strip()}")
    return res


# ---------------------------------------------------------------------------
# 1. Pull latest code
# ---------------------------------------------------------------------------
def git_pull():
    log("Running git pull ...")
    run_git(["pull", "--rebase", "origin", "main"], check=False)


# ---------------------------------------------------------------------------
# 2 & 3. Load data + data-quality validation
# ---------------------------------------------------------------------------
def load_records():
    log(f"Loading records from {os.path.abspath(DATA_PATH)} ...")
    with open(DATA_PATH, "r", encoding="utf-8") as fh:
        records = json.load(fh)
    log(f"Loaded {len(records)} records")
    return records


def validate_quality(records):
    """Return dict of data-quality issues."""
    issues = defaultdict(list)
    seen_ids = {}
    for rec in records:
        if not isinstance(rec, dict):
            issues["not_object"].append(rec)
            continue
        sid = rec.get("service_id")
        # duplicate service_id
        if sid is not None:
            if sid in seen_ids:
                issues["duplicate_service_id"].append(sid)
            else:
                seen_ids[sid] = True
        # missing key fields
        for field in KEY_FIELDS:
            if field not in rec or rec[field] is None or rec[field] == "":
                issues[f"missing_{field}"].append(sid)
        # non-UK/FR/DE country
        country = rec.get("country")
        if country is not None and country not in VALID_COUNTRIES:
            issues["non_eu_country"].append(sid)
    return issues


def summarize_quality(issues):
    """Produce readable summary lines and a total count of issue instances."""
    summary = []
    total = 0
    for key in sorted(issues.keys()):
        items = issues[key]
        if not items:
            continue
        if key == "duplicate_service_id":
            n_unique = len(set(items))
            label = f"Duplicate service_id ({n_unique} ids repeated across {len(items)} extra occurrences)"
        elif key == "non_eu_country":
            label = f"Non-UK/FR/DE country records ({len(items)})"
        elif key.startswith("missing_"):
            field = key[len("missing_"):]
            label = f"Missing key field '{field}' ({len(items)} records)"
        else:
            label = f"{key} ({len(items)})"
        summary.append(label)
        total += len(items)
    return summary, total


# ---------------------------------------------------------------------------
# 4. Per-country statistics
# ---------------------------------------------------------------------------
def parse_date(s):
    try:
        return datetime.date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def country_stats(records):
    stats = {}
    for country in sorted(VALID_COUNTRIES):
        subset = [r for r in records
                  if r.get("country") == country and isinstance(r, dict)]
        n = len(subset)
        progresses = [r.get("progress") for r in subset
                      if isinstance(r.get("progress"), (int, float))]
        avg_progress = round(sum(progresses) / len(progresses), 2) if progresses else 0.0
        overdue = 0
        risk_dist = Counter()
        for r in subset:
            rl = r.get("risk_level")
            if rl is not None:
                risk_dist[rl] += 1
            due = parse_date(r.get("due_date"))
            if due is not None and due < datetime.date.today() and r.get("status") != "completed":
                overdue += 1
        stats[country] = {
            "project_count": n,
            "average_progress": avg_progress,
            "overdue_count": overdue,
            "risk_distribution": dict(risk_dist),
        }
    return stats


# ---------------------------------------------------------------------------
# 5. High-risk list
# ---------------------------------------------------------------------------
def high_risk_projects(records):
    today = datetime.date.today()
    flagged = []
    for r in records:
        if not isinstance(r, dict):
            continue
        status = r.get("status")
        risk = r.get("risk_level")
        due = parse_date(r.get("due_date"))
        blocked = status == "blocked"
        risky_overdue = risk in HIGH_RISK_LEVELS and due is not None and due < today
        if blocked or risky_overdue:
            flagged.append(r)
    # sort: blocked first, then critical before high, then earliest due date
    def sort_key(r):
        risk_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(r.get("risk_level"), 4)
        status_rank = 0 if r.get("status") == "blocked" else 1
        return (status_rank, risk_rank, r.get("due_date") or "9999-12-31")
    flagged.sort(key=sort_key)
    return flagged


# ---------------------------------------------------------------------------
# 6. Build README dashboard
# ---------------------------------------------------------------------------
def build_readme(records, stats, high_risk, quality_summary, quality_total):
    total = len(records)
    now = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = []
    lines.append("# EU Government Public Service Portfolio — Daily Health Dashboard")
    lines.append("")
    lines.append(f"> Auto-generated by `scripts/sync_portfolio.py` · Last update: **{now}**")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append(f"- **Total records tracked**: {total}")
    lines.append(f"- **Countries**: United Kingdom 🇬🇧 / France 🇫🇷 / Germany 🇩🇪")
    lines.append(f"- **High-risk projects**: **{len(high_risk)}**")
    lines.append(f"- **Data-quality issues**: **{quality_total}**")
    lines.append("")

    lines.append("## Country Comparison (UK / FR / DE)")
    lines.append("")
    lines.append("| Country | Projects | Avg Progress | Overdue | Risk Distribution (low/med/high/crit) |")
    lines.append("|---------|---------:|-------------:|--------:|--------------------------------------:|")
    for c in ["UK", "FR", "DE"]:
        s = stats[c]
        rd = s["risk_distribution"]
        risk_str = f"{rd.get('low',0)}/{rd.get('medium',0)}/{rd.get('high',0)}/{rd.get('critical',0)}"
        lines.append(f"| {c} | {s['project_count']} | {s['average_progress']:.1f}% | {s['overdue_count']} | {risk_str} |")
    lines.append("")

    lines.append("## High-Risk Projects")
    lines.append("")
    lines.append(f"Criteria: `status == blocked` **or** (`risk_level` in critical/high **and** `due_date < today`). "
                 f"Total flagged: **{len(high_risk)}**. Showing top {min(TOP_N_README, len(high_risk))}:")
    lines.append("")
    lines.append("| # | Country | Service ID | Service Name | Status | Priority | Due Date | Risk | Progress |")
    lines.append("|---|---------|-----------|--------------|--------|----------|----------|------|---------:|")
    for i, r in enumerate(high_risk[:TOP_N_README], 1):
        lines.append(
            f"| {i} | {r.get('country','')} | {r.get('service_id','')} | {str(r.get('service_name',''))[:40]} | "
            f"{r.get('status','')} | {r.get('priority','')} | {r.get('due_date','')} | "
            f"{r.get('risk_level','')} | {r.get('progress','')} |"
        )
    if len(high_risk) > TOP_N_README:
        lines.append(f"| … | | | *and {len(high_risk) - TOP_N_README} more* | | | | | |")
    lines.append("")
    lines.append(f"*Full high-risk list (all {len(high_risk)}) saved to `reports/high_risk_projects.json` for audit.*")
    lines.append("")

    lines.append("## Data Quality Issues")
    lines.append("")
    if quality_summary:
        lines.append(f"Total issue instances: **{quality_total}**")
        lines.append("")
        lines.append("| Issue | Count |")
        lines.append("|-------|------:|")
        for q in quality_summary:
            # extract count from label tail "(N)"
            count = ""
            if q.rstrip().endswith(")"):
                count = q.rsplit("(", 1)[1][:-1]
            lines.append(f"| {q} | {count} |")
        lines.append("")
    else:
        lines.append("No data-quality issues detected. ✅")
        lines.append("")

    lines.append("---")
    lines.append("*This dashboard is regenerated daily by the `Daily EU Gov Portfolio Sync` workflow.*")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 7. Commit & push
# ---------------------------------------------------------------------------
def git_commit_push():
    log("Staging changes ...")
    run_git(["add", "data/eu_public_service_portfolio.json", "README.md", "reports/"])
    run_git(["add", "-A"])
    # only commit if there are staged changes
    res = run_git(["diff", "--cached", "--quiet"], check=False)
    if res is not None and res.returncode == 0:
        log("No changes to commit.")
        return False
    run_git(["commit", "-m", f"Daily EU Gov Portfolio sync {TODAY}"])
    log("Pushing to origin/main ...")
    run_git(["push", "origin", "main"])
    return True


# ---------------------------------------------------------------------------
# 8. Maintain GitHub issue (gh issue list / create / edit equivalent)
# ---------------------------------------------------------------------------
def gh_api(path, method="GET", data=None):
    import urllib.request
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        log("WARNING: GITHUB_TOKEN not set, skipping issue sync")
        return None
    url = f"https://api.github.com{path}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "eu-gov-portfolio-sync")
    body = json.dumps(data).encode() if data is not None else None
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=body, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except Exception as e:
        log(f"WARNING: GitHub API {method} {path} failed: {e}")
        return None


def sync_issue(high_risk_count, quality_total, stats):
    """gh issue list + gh issue create/edit behaviour."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        return
    # gh issue list --search "title:Daily EU Gov Portfolio Sync" (find existing)
    result = gh_api(f"/search/issues?q=repo:{REPO_OWNER}/{REPO_NAME}+in:title+{urllib_quote(ISSUE_TITLE)}")
    existing = None
    if result:
        _, payload = result
        for item in payload.get("items", []):
            if item.get("title") == ISSUE_TITLE and item.get("pull_request") is None:
                existing = item
                break

    body = build_issue_body(high_risk_count, quality_total, stats)

    if existing:
        # gh issue edit <number> --title ... --body ...
        gh_api(f"/repos/{REPO_OWNER}/{REPO_NAME}/issues/{existing['number']}",
               method="PATCH",
               data={"title": ISSUE_TITLE, "body": body})
        log(f"Updated issue #{existing['number']} ({ISSUE_TITLE})")
    else:
        # gh issue create --title ... --body ...
        gh_api(f"/repos/{REPO_OWNER}/{REPO_NAME}/issues",
               method="POST",
               data={"title": ISSUE_TITLE, "body": body})
        log(f"Created issue ({ISSUE_TITLE})")


def urllib_quote(s):
    import urllib.parse
    return urllib.parse.quote(s)


def build_issue_body(high_risk_count, quality_total, stats):
    now = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    rows = "".join(
        f"| {c} | {stats[c]['project_count']} | {stats[c]['average_progress']:.1f}% | "
        f"{stats[c]['overdue_count']} | "
        f"{stats[c]['risk_distribution'].get('critical',0)} |\n"
        for c in ["UK", "FR", "DE"]
    )
    return (
        f"## Daily EU Gov Portfolio Sync — {TODAY}\n\n"
        f"**High-risk projects:** {high_risk_count}\n\n"
        f"**Data-quality issues:** {quality_total}\n\n"
        f"### Country snapshot\n"
        f"| Country | Projects | Avg Progress | Overdue | Critical Risk |\n"
        f"|---------|---------:|-------------:|--------:|--------------:|\n"
        f"{rows}\n"
        f"_Generated at {now} by the automated daily sync. See README.md for the full dashboard._\n"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    start = datetime.datetime.now()
    git_pull()

    records = load_records()
    issues = validate_quality(records)
    quality_summary, quality_total = summarize_quality(issues)

    stats = country_stats(records)
    high_risk = high_risk_projects(records)

    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(HIGH_RISK_REPORT, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": TODAY, "count": len(high_risk),
                   "projects": high_risk}, fh, ensure_ascii=False, indent=2)

    readme = build_readme(records, stats, high_risk, quality_summary, quality_total)
    with open(README_PATH, "w", encoding="utf-8") as fh:
        fh.write(readme)
    log("README.md written")

    git_commit_push()
    sync_issue(len(high_risk), quality_total, stats)

    elapsed = (datetime.datetime.now() - start).total_seconds()
    log(f"Sync complete in {elapsed:.2f}s")
    log(f"High-risk: {len(high_risk)} | Quality issues: {quality_total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
