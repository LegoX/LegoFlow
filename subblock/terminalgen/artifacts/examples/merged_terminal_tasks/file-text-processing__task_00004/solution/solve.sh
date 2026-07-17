#!/bin/bash

# Script to extract JSON key names using jq
# Task: Extract key names from version.json and write to keys.txt
# Input:  /app/task_file/input/version.json
# Output: /app/task_file/output/keys.txt

set -e

# Define file paths
INPUT_FILE="/app/task_file/input/version.json"
OUTPUT_FILE="/app/task_file/output/keys.txt"

# Validate input file exists
if [ ! -f "$INPUT_FILE" ]; then
    echo "Error: Input file not found: $INPUT_FILE" >&2
    exit 1
fi

# Validate output directory exists
if [ ! -d "$(dirname "$OUTPUT_FILE")" ]; then
    echo "Error: Output directory does not exist" >&2
    exit 1
fi

# Extract JSON keys in order they appear and write to output file
# -r flag outputs raw strings without quotes
# keys_unsorted[] extracts keys in original order and iterates through them
jq -r 'keys_unsorted[]' "$INPUT_FILE" > "$OUTPUT_FILE"

# Verify output file was created and is not empty
if [ ! -s "$OUTPUT_FILE" ]; then
    echo "Error: Failed to create output file or file is empty" >&2
    exit 1
fi

echo "Success: Keys extracted to $OUTPUT_FILE"
exit 0