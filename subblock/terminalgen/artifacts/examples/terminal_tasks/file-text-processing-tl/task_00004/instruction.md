# Extract JSON Key Names with jq

## Task Description

You are given a JSON file containing application version information with multiple key-value pairs. Your task is to extract only the **key names** from the JSON object using `jq`, rather than the values.

This task simulates a real-world scenario where you need to analyze JSON API responses and identify the available fields (keys) in the data structure without seeing their values.

## Working Directory

```
/app/task_file/
├── input/
│   └── version.json
└── output/
    └── keys.txt
```

## Input Data

A JSON file located at `/app/task_file/input/version.json` containing:

```json
{
  "email": "madireddy@test.com",
  "versionID": "2323",
  "context": "test",
  "date": "02-03-2014-13:41",
  "versionName": "application"
}
```

## Your Challenge

Using `jq`, extract **only the key names** from the JSON object in `/app/task_file/input/version.json` and write them to `/app/task_file/output/keys.txt`, with each key name on a new line.

You may use either:
- Direct piping from a curl command (if testing against a live endpoint)
- Reading from the provided input file
- A combination of both approaches

## Expected Output

The file `/app/task_file/output/keys.txt` should contain:

```
email
versionID
context
date
versionName
```

## Success Criteria

✓ The output file `/app/task_file/output/keys.txt` exists  
✓ Each key name from the JSON appears on its own line  
✓ The key names are extracted in the correct order  
✓ No values are included in the output  
✓ No extra whitespace or formatting is present (other than newlines between entries)  
✓ The solution uses `jq` to process the JSON data  

## Hints

- Use `jq 'keys[]'` to extract and iterate through keys
- Consider using pipes (`|`) to chain jq commands
- Redirect output to the specified file location using `>`