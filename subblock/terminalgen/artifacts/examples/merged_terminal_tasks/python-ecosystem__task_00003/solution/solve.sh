#!/bin/bash

# CSV to JSONL Conversion Script
# Converts CSV file to newline-delimited JSON format
# Input: /app/task_file/input/data.csv
# Output: /app/task_file/output/data.json

set -euo pipefail

# Define paths
INPUT_FILE="/app/task_file/input/data.csv"
OUTPUT_FILE="/app/task_file/output/data.json"
OUTPUT_DIR="/app/task_file/output"

# Check if input file exists
if [[ ! -f "$INPUT_FILE" ]]; then
    echo "Error: Input file not found at $INPUT_FILE" >&2
    exit 1
fi

# Ensure output directory exists
mkdir -p "$OUTPUT_DIR"

# Run Python script to convert CSV to JSONL
python3 << 'PYTHON_EOF'
import csv
import json
import sys

# Configuration
input_file = "/app/task_file/input/data.csv"
output_file = "/app/task_file/output/data.json"
fieldnames = ("FirstName", "LastName", "IDNumber", "Message")

try:
    with open(input_file, 'r') as csvfile:
        with open(output_file, 'w') as jsonfile:
            # Create CSV reader with specified field names
            reader = csv.DictReader(csvfile, fieldnames=fieldnames)
            
            # Process each row and write as JSON on separate line
            for row in reader:
                # Skip empty rows
                if any(row.values()):
                    json.dump(row, jsonfile)
                    jsonfile.write('\n')
    
    print("Conversion completed successfully!", file=sys.stderr)
except Exception as e:
    print(f"Error during conversion: {e}", file=sys.stderr)
    sys.exit(1)
PYTHON_EOF

# Verify output file was created
if [[ ! -f "$OUTPUT_FILE" ]]; then
    echo "Error: Output file was not created at $OUTPUT_FILE" >&2
    exit 1
fi

# Verify output file has content
if [[ ! -s "$OUTPUT_FILE" ]]; then
    echo "Error: Output file is empty" >&2
    exit 1
fi

# Count lines in output file
line_count=$(wc -l < "$OUTPUT_FILE")
echo "Output file created: $OUTPUT_FILE"
echo "Total JSON objects written: $line_count"

exit 0