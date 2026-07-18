# CSV to Multiline JSON Conversion Task

## Objective
Convert a CSV file into JSON Lines format (JSONL), where each CSV record becomes a separate JSON object on its own line.

## Problem Description
You have a CSV file that needs to be converted to JSON format. However, instead of a single JSON array with all records, you need each record to appear as a separate JSON object on its own line. This format is also known as JSON Lines (JSONL) or newline-delimited JSON (NDJSON).

### Current Behavior (Incorrect)
```
[{"FirstName":"John","LastName":"Doe","IDNumber":"123","Message":"None"},{"FirstName":"George","LastName":"Washington","IDNumber":"001","Message":"Something"}]
```

### Desired Behavior (Correct)
```
{"FirstName":"John","LastName":"Doe","IDNumber":"123","Message":"None"}
{"FirstName":"George","LastName":"Washington","IDNumber":"001","Message":"Something"}
```

## Requirements
1. Read the CSV file from `/app/task_file/input/data.csv`
2. Parse the CSV file using the field names: `FirstName`, `LastName`, `IDNumber`, `Message`
3. Convert each row into a separate JSON object
4. Write each JSON object on its own line to the output file
5. Save the output to `/app/task_file/output/data.json`

## Input File
**Location:** `/app/task_file/input/data.csv`

**Format:** Standard CSV with 4 fields (no header row in the file itself)

**Sample Content:**
```
"John","Doe","001","Message1"
"George","Washington","002","Message2"
"Jane","Smith","003","Message3"
"Benjamin","Franklin","004","Message4"
```

## Output File
**Location:** `/app/task_file/output/data.json`

**Expected Format:** JSONL (one JSON object per line)

**Expected Content:**
```
{"FirstName":"John","LastName":"Doe","IDNumber":"001","Message":"Message1"}
{"FirstName":"George","LastName":"Washington","IDNumber":"002","Message":"Message2"}
{"FirstName":"Jane","LastName":"Smith","IDNumber":"003","Message":"Message3"}
{"FirstName":"Benjamin","LastName":"Franklin","IDNumber":"004","Message":"Message4"}
```

## Success Criteria
1. ✓ The output file exists at `/app/task_file/output/data.json`
2. ✓ Each CSV row is converted to exactly one JSON object
3. ✓ Each JSON object is on its own separate line
4. ✓ No JSON array wrapper around the entire output
5. ✓ All four fields (FirstName, LastName, IDNumber, Message) are correctly mapped from the CSV
6. ✓ The JSON objects are valid and properly formatted
7. ✓ The output file contains the correct number of lines matching the input file (minus any empty lines)

## Implementation Notes
- You can use Python with the `csv` and `json` modules
- Consider using `json.dumps()` for each individual row instead of creating an array
- Ensure each line in the output file ends with a newline character
- Handle edge cases such as CSV values containing quotes or commas appropriately