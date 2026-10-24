# Synthetic Terminal Curator demo

`fixture.json` is a small, hand-declared UI fixture. Every task, batch and run ID
starts with `demo-`, dates are fixed, and all counts/outcomes are illustrative.
It contains no real task content, run identifiers, host paths, endpoint details,
profiles or credentials. It is not evidence that curation or rollout occurred.

`index.html` is the single tracked, self-contained demo snapshot, following the
existing Curator docs convention. From the terminal-curator block directory:

```bash
artifacts/env/terminal-curator/bin/python dashboard/build_demo.py
artifacts/env/terminal-curator/bin/python dashboard/build_demo.py --check
```

The builder uses the current dashboard renderer and only this declared fixture;
it never calls `snapshot()` or reads local runtime artifacts. It inserts the
visible synthetic-data notice and disables live API polling. Changing the UI
requires regenerating this one snapshot. The ordinary docs prebuild/predev
step stages an ignored copy into the root docs public directory. Deployment
must not replace this demo with real run output; real reviewed snapshots are
shared separately when requested.
