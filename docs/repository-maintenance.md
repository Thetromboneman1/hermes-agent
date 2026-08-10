<!-- markdownlint-disable MD013 -->

# Repository maintenance

Last audited: 2026-08-10

## Purpose and role

The agent that grows with you

- Visibility: `public`
- Maintenance status: `active`
- Repository roles: `GitHub fork`, `downstream customization`, `container repository`, `deployment repository`, `build repository`
- Authoritative upstream: `NousResearch/hermes-agent`
- Downstream consumers: Hermes Desktop through the optional loopback-bound SSH container path.

## Upstream synchronization

- Strategy: merge upstream into a dedicated review branch and rebuild downstream overlays
- Automation status: `configured`
- Schedule and manual recovery: use the repository's registered upstream-sync workflow when configured.
- Safety rule: synchronize through a dedicated review branch; never discard downstream commits or force-push a shared branch.
- `local-first-acp-optimizations` is reconstructed on current `main`; its intentional
  differences are recorded in `.github/downstream-config-manifest.yml`. The
  pre-reconstruction history is preserved at
  `backup/local-first-acp-optimizations-pre-rebase-20260810`.

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
- The optional SSH service uses key-only root authentication and starts only
  when `HERMES_HOME/.ssh/authorized_keys` is a regular file. Publish container
  port 22 only on a loopback host address; the image does not expose it by
  default.

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
