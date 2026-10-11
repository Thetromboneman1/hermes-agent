# Upstream reference workflow contracts

These exact upstream YAML files are inert test fixtures, not deployed fork workflows.
The adjacent manifest pins the upstream repository, commit, original paths and SHA-256
of every archived file. The explicit `reference_workflow_path` loader checks integrity;
it never looks for a missing deployed workflow or substitutes one automatically.

Release, publication, plugin-confinement and Windows crash harnesses replay these
reference steps against the current scripts and disposable local transports. Their
results establish compatibility with those upstream contracts, not hosted execution,
Windows qualification or a deployed release pipeline on this fork. The archived Nix
release workflow is also distinct from the fork's live, bounded Nix validation workflow.

Live fork dispatch, checkout, permissions, aggregation and smoke selection are tested
against `.github/workflows/` directly. CI routing fixtures (`ci.yaml`, `tests.yml`,
`tests-os.yml`) are deliberately not archived or substituted.

On an upstream sync, refresh only the files enumerated in `manifest.json` using
`git show <resolved-upstream-commit>:<source_path>`, preserving the bytes verbatim.
Recompute each SHA-256 and set `source_commit` to that full commit ID atomically
with the fixture update. A missing upstream source is a sync failure requiring review,
not permission to drop the fixture or its tests. Do not copy these files into
`.github/workflows/`; the archive does not authorize enabling their runners or releases.
