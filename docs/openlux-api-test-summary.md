# OpenLux API Test Summary

The validated configuration used the OpenLux base URL
`https://api.openlux.ai/v1` and model `gpt-5.6-sol`. Credentials were supplied
outside the repository.

## Results

- `GET /v1/models`: HTTP 200.
- `POST /v1/responses`: HTTP 200 with completed usable output.
- `POST /v1/chat/completions`: HTTP 200 in the Claude-compatible test path.
- Codex CLI basic response: passed.
- LegoFlow root plugin invocation: passed.

The `responses` endpoint is the load-bearing requirement for Codex. Chat
Completions success alone does not establish Codex compatibility.
