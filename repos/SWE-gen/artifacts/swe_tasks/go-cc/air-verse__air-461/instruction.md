Implement support for `pre_cmd` and `post_cmd` configuration options in the air build configuration.

Currently, air only supports a single `cmd` field in the build configuration. Users want to run commands before each build (e.g., `swag fmt` and `swag init` before a Go build) and after air exits (cleanup scripts, etc.).

## Wanted Configuration

```toml
[build]
    pre_cmd = ["swag fmt", "swag init"]
    cmd = "go build -o ./tmp/main.exe ."
    post_cmd = ["some other scripts"]
```

## Expected Behavior

- `pre_cmd` is an array of shell commands that are executed **one by one before each build** (i.e., before `cmd` runs on every file change)
- `post_cmd` is an array of shell commands that are executed **upon exiting** (i.e., when the user hits Ctrl+C / air stops)
- Both fields should be optional (empty by default)
- The `cfgBuild` struct should include `PreCmd []string` and `PostCmd []string` fields
- The default configuration should include these fields with empty defaults
- The engine should execute `pre_cmd` commands sequentially before building, and `post_cmd` commands sequentially on shutdown

## Example Usage

A user running a Fiber API wants to regenerate Swagger docs before every build:

```toml
[build]
  pre_cmd = ["swag fmt", "swag init"]
  cmd = "go build -o ./tmp/main ."
  post_cmd = ["echo done"]
```

With this config, every time air detects a file change it should:
1. Run `swag fmt`
2. Run `swag init`
3. Run `go build -o ./tmp/main .`

And when air exits, it should run `echo done`.