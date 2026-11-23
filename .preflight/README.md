# Git Preflight

Canonical source: `jwedow/AtlasAX-ops/tools/git-preflight`. Each enrolled repository vendors the same runner into `.preflight/`, alongside its own reviewed `manifest.json`. GitHub and local pushes execute that same manifest. Existing required GitHub checks remain authoritative, including checks explicitly listed as cloud-only.

## Install In A Checkout

Run `python3 .preflight/install.py` after cloning, and again after updating the shared runner. This installs a repository-local dispatcher; it does not change global Git settings or overwrite previous hooks. The previous pre-push hook receives the original arguments and complete stdin before validation. Other installed hooks delegate to their original paths. Run the installer again after adding new hooks to the original directory.

The installer is explicit because cloning cannot safely auto-execute repository code. GitHub enforcement also covers uninstalled hooks, API changes and cloud agents. Worktree-specific or command-level hook overrides must be reconciled before installation; the installer refuses to hide them.

## Run

`python3 .preflight/preflight.py --base <verified-base-sha>` runs the manifest in the current checkout (for CI or deliberate diagnostics). The installed pre-push hook instead checks isolated copies of every distinct pushed commit, including non-HEAD refs. It fetches the current target base and does not rebase or change working files. Deletions alone require no source checks. Missing tools, dependencies, source or manifest fail the push with a concrete message.

Tasks have `id`, `command` (argv array), optional `cwd`, `needs`, and `timeout` (seconds, maximum 3600). At most two independent tasks run together by default. All independent failures are collected; dependents of failed checks show SKIP, never PASS. Dependency cycles and missing dependency IDs are invalid. There is no pass-receipt cache in version 1: a changed commit always receives fresh checks. Existing package-manager caches may accelerate dependency installation.

Application checks run with a temporary HOME and an allowlisted environment, not the developer's exported credentials. This reduces accidental live access; it is **not an OS sandbox for hostile source**. Manifests must contain only reviewed validation commands, never deployment, financial operations, live ingestion or migrations. Synthetic build configuration can be declared explicitly in `environment`. Cloud-only checks are printed separately and do not become local passes.

## Removal

Read the saved original hook directory in `git rev-parse --git-common-dir` / `aulron-preflight-hooks/state.json`. Restore that path with repository-local `git config core.hooksPath <previous>`. Original hook files are untouched. Keep the dispatcher directory until restoration is verified; do not delete personal hooks.

## Tests

`python3 -m unittest discover -s .preflight/tests -v` (or `tools/git-preflight/tests` in this source repository) creates disposable repositories and remotes. It proves actual push blocking, success, dirty-source isolation, changed commits, multi-ref/non-HEAD pushes, deletion, failure aggregation, timeouts, dependency validation, fetched-base availability and original hook preservation. No live repository is pushed by these tests.

The installer refuses relative `core.hooksPath` values before changing configuration: Git resolves those differently across worktrees and hook types. Reconcile that existing setup explicitly first. Absolute and home-relative (`~/`) paths are supported; original hook files are never edited. Removed or disabled original hooks are skipped, matching Git behavior. Installation works from any subdirectory.
