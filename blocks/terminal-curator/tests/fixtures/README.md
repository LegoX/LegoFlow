These are hand-authored, deterministic offline fixtures, not examples generated
from StackOverflow and not evidence of an LLM generation run.

Both tasks require terminal file processing and exercise edge cases:

- `terminal-jsonl-summary`: recursively aggregate logs, reject malformed records,
  distinguish booleans from integers, trim service names, sort UTF-8 names, and
  compute error counts and nearest-rank percentiles.
- `terminal-csv-reconcile`: parse quoted and multiline CSV fields, normalize
  emails, reconcile numeric revisions with deterministic tie breaks, process
  deletion/restoration, and produce sorted CSV.

Their reference solutions need only Python's standard library. The Docker
image installs the pinned pytest dependency during the build; task execution
and verification need no network. Tests assert complete expected outputs and
hash all input files to detect accidental input mutation. A fresh task must
fail verification before any solution is executed.
