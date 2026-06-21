# Git Workflow

## Model: Trunk-Based Development

`main` is always deployable. Small changes go directly to `main`; feature branches are used
only when a change needs multiple sessions or carries meaningful risk to the working system.

---

## Commit to `main` Directly When

- Bug fixes and hotfixes
- Single-session features (< half a day of work, clearly scoped)
- Docs, config changes, refactoring, test additions
- Changes to a single service boundary that don't require coordination

## Use a Feature Branch When

- Work spans multiple conversations or days
- The change is experimental (new architecture, significant refactor)
- You want a review gate before the work lands on `main`
- The change touches multiple subsystems and needs a coherent diff to review

Branch naming: `feat/<description>`, `fix/<description>`, `refactor/<description>`

```bash
git checkout -b feat/correction-export
# work, commit normally
git checkout main && git merge --no-ff feat/correction-export
git branch -d feat/correction-export
```

No squash merges — preserve the commit history from the branch.

---

## Commit Message Format

```
<type>(<scope>): <one-line summary>

Body if needed: explain WHY, not what. Reference issue numbers here.
```

| `type` | Use for |
|--------|---------|
| `feat` | New user-visible capability |
| `fix` | Bug fix |
| `refactor` | Code change with no behaviour change |
| `chore` | Build, deps, config, CI |
| `docs` | Documentation only |
| `test` | Tests only |

`scope` (optional): `pipeline`, `api`, `frontend`, `stages`, `diarize`, `worker`, `dag`

Examples:
```
feat(api): add GET /jobs/{id}/events route
fix(dag): reset started_at on each stage success so watchdog doesn't misfire
refactor(worker): move xack to immediate; watchdog owns crash recovery
docs: add architecture.md from excalidraw
```

---

## Rules

| Rule | Detail |
|------|--------|
| `main` is always green | Never push a broken build |
| No force-push to `main` | Non-negotiable |
| Do not push unless asked | User confirms each remote push |
| Atomic commits | One logical change per commit; split large changes |
| No `--no-verify` | Fix the hook, don't skip it |

---

## Tagging a Release

See [`docs/releases.md`](releases.md) for the version table, unreleased changelog,
and the pre-release checklist before running these commands.

```bash
# 1. Confirm tests pass
source venv/bin/activate && pytest tests/ -q --ignore=tests/web

# 2. Tag
git tag -a v<MAJOR>.<MINOR>.<PATCH> -m "v<MAJOR>.<MINOR>.<PATCH> — one-line summary"

# 3. Update docs/releases.md: move unreleased → history table, reset unreleased section

# 4. Push tag (when ready to publish)
git push origin v<MAJOR>.<MINOR>.<PATCH>
```

### Version Semantics

| Increment | When |
|-----------|------|
| MAJOR | Breaking API or pipeline contract change; full architecture redesign |
| MINOR | New feature, new endpoint, new pipeline stage, significant improvement |
| PATCH | Bug fix, performance, docs, minor UX polish |
