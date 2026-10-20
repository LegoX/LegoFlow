Build a deterministic service report from the JSON Lines files under
`/app/task_file/input/`, including files in nested directories.

A valid record is a JSON object with a nonempty string `service`, integer
`timestamp` at least `1700000000`, integer `status` between 100 and 599, and
nonnegative integer `latency_ms`. Trim whitespace from service names. Booleans
are not integers for these rules. Ignore blank lines and every record that
does not meet these rules, including malformed JSON and old timestamps.

Write `/app/task_file/output/summary.json` as a JSON array, sorted by service
name. Each item must contain exactly `service`, `requests`, `errors`,
`max_latency_ms`, and `p95_latency_ms`. Count a request as an error when its
status is at least 500. Calculate p95 by sorting a service's latencies and
taking the value at the one-based position `ceil(0.95 * requests)`.

Do not modify or delete any input files. Create the output directory if needed.
Use only tools already installed in the environment. No network is needed.
