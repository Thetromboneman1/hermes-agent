<!-- markdownlint-disable MD013 -->

# Repository maintenance

Last audited: 2026-07-26

## Purpose and role

The agent that grows with you

- Visibility: `public`
- Maintenance status: `active`
- Repository roles: `GitHub fork`, `downstream customization`, `container repository`, `deployment repository`, `build repository`
- Authoritative upstream: `NousResearch/hermes-agent`
- Downstream consumers: None documented.

## Upstream synchronization

- Strategy: review genuine upstream conflicts on a repair branch; scheduled clean merges retain the fork workflow set and validate the exact synchronized revision.
- Automation status: `configured`
- Schedule and manual recovery: use the repository's registered upstream-sync workflow when configured.
- Safety rule: never choose upstream wholesale to resolve product conflicts. Fork-only
  documentation keeps its existing paths when upstream moves directories. The merge
  helper aborts and restores the clean starting tree on any non-workflow conflict.
- Validation: the sync workflow calls the retained tests, Nix, lockfile, documentation
  and container checks explicitly because a GITHUB_TOKEN push does not trigger them.
  Custom-overlay propagation waits for all six exact-SHA validators and preserves a
  remote backup. It merges the validated main commit without rebasing or force-pushing,
  retaining the original overlay commits and reviewed merge-only reconciliation.
  The historical `rebase` strategy is superseded for this history-preserving workflow;
  the `force_rebase` input and job ID remain compatible names for forced propagation.
  Every successful main-validation run checks actual remote ancestry, even when main
  did not change, so failed prior custom propagation is retried. If custom already
  contains the validated main commit, backup, custom validation and publication are
  skipped unless `force_rebase` requests revalidation; it never permits a force push.
- Custom-branch conflicts: source, workflow, and human README conflicts fail closed.
  Only the marked `documentation-health` README block and the wholly generated
  `docs/REPOSITORY_STATE.md` may be regenerated. Human README text is three-way merged,
  never replaced wholesale; ambiguous markers and unsupported CR/CRLF README line
  endings require manual review. A failed merge or documentation check restores the clean
  starting branch. The prepared commit stays detached until its focused overlay tests,
  container build, CLI smoke, and read-only SSH image policy check all pass. Missing
  custom validation files fail closed. The custom branch owns its executable validation
  entrypoint, shared by its pull-request job and scheduled propagation; overlay tests
  are checked in that prepared custom tree, not copied onto main.
  Publication requires unchanged observed remote
  heads, preserved ancestry, a clean exact tested commit, and a normal fast-forward
  push. Concurrent divergence is never repaired by force or hidden replay.

## Workflows

- `.github/workflows/contributor-check.yml`
- `.github/workflows/deploy-site.yml`
- `.github/workflows/docker-publish.yml`
- `.github/workflows/docs-site-checks.yml`
- `.github/workflows/nix-lockfile-check 2.yml`
- `.github/workflows/nix-lockfile-check.yml`
- `.github/workflows/nix.yml`
- `.github/workflows/skills-index.yml`
- `.github/workflows/supply-chain-audit.yml`
- `.github/workflows/tests.yml`
- `.github/workflows/upstream-sync.yml`

- Current Actions status at audit: `success`
- Downstream rebuild status: `configured`
- Workflow concurrency and permissions are defined in the workflow files and validated by the fleet collector.

## Build, rebuild, and validation

- Build systems: `Python/pyproject`, `npm/node`
- Container tooling: `Docker`, `Docker Compose`
- Deployment tooling: `GitHub Actions`
- Run repository-specific build and test commands documented in the root README before promotion.
- Validate workflow changes with `actionlint`, parse structured configuration, and inspect the resulting GitHub Actions run.
- For upstream changes, verify downstream configuration and generated artifacts before merging the review branch.

## Configuration and secrets

Secret values must never be committed or written to logs. Approved sensitive
material belongs in the 1Password vault `Boneman`; only names are documented.

### GitHub secret references

- `DOCKERHUB_TOKEN`
- `DOCKERHUB_USERNAME`
- `GITHUB_TOKEN`
- `VERCEL_DEPLOY_HOOK`

### GitHub variable references

None documented.

## Notifications

- Lifecycle email status: `missing`
- A notification failure must remain visible but must not replace the primary workflow result.
- Duplicate delivery is prevented at the reusable notification layer when notification credentials are available.

## Local development

1. Use the tracked default branch `main`.
2. Preserve uncommitted work before changing remotes or branch tracking.
3. Install dependencies with the repository's documented package or build tool.
4. Run focused tests, then the full supported validation suite.
5. Push an intentional commit and inspect the resulting workflow run.

## Recovery

- Revert the smallest repository-specific commit to roll back an automation or documentation change.
- Close an upstream-sync pull request and delete only its dedicated automation branch to abandon an unmerged synchronization.
- Restore generated or deployment artifacts from the last verified commit or release; do not rewrite published history without a separate recoverable checkpoint.
- Re-run the failed workflow only after the failure fingerprint or configuration has changed.

## Known limitations

- Fleet-standard lifecycle email is not active until the approved Boneman-scoped non-interactive credential path and a public-repository sender adapter are available.
- The fork is intentionally divergent; upstream changes must use the configured review branch and preserve downstream commits.

## Ownership

Owned and administered by `Thetromboneman1`. The next scheduled fleet
maintenance review is 2026-08-02, or immediately after a failed workflow,
upstream-sync conflict, or notification health failure.
