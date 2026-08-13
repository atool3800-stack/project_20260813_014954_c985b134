# EU Government Public Service Portfolio — Daily Sync

Automated daily portfolio health dashboard for **UK / France / Germany**
government public service digital programmes.

## Repository layout

- `data/eu_public_service_portfolio.json` — source portfolio records (12000+)
  with fields: `country`, `service_id`, `service_name`, `owner`, `status`,
  `priority`, `due_date`, `risk_level`, `progress`.
- `scripts/sync_portfolio.py` — the daily sync engine (parse, validate,
  analyse, write README, commit/push, update issue).
- `.github/workflows/daily-sync.yml` — scheduled daily execution (06:00 UTC).
- `reports/high_risk_projects.json` — full high-risk list for audit.
- `README.md` — regenerated health dashboard.

## Running manually

```bash
export GITHUB_TOKEN=<your token>
python scripts/sync_portfolio.py
```

The script performs: `git pull` → parse JSON → data-quality validation →
per-country stats → high-risk filtering → README regeneration →
`git add/commit/push` → issue create/update (`Daily EU Gov Portfolio Sync`).
