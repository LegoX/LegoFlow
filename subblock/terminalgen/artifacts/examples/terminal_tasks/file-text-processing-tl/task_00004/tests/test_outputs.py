import os
import pytest


def test_output_file_exists():
    """Test that the output file was created"""
    output_file = "/app/task_file/output/keys.txt"
    assert os.path.exists(output_file), f"Output file not found: {output_file}"


def test_output_file_not_empty():
    """Test that the output file is not empty"""
    output_file = "/app/task_file/output/keys.txt"
    assert os.path.getsize(output_file) > 0, "Output file is empty"


def test_output_contains_correct_keys_in_order():
    """Test that output contains all expected keys in correct order"""
    output_file = "/app/task_file/output/keys.txt"
    expected_keys = ["email", "versionID", "context", "date", "versionName"]
    
    with open(output_file, 'r') as f:
        content = f.read()
    
    # Get lines, strip whitespace, and filter empty lines (handles trailing newline)
    actual_keys = [l for l in content.strip().splitlines() if l.strip()]
    
    assert len(actual_keys) == len(expected_keys), f"Expected {len(expected_keys)} keys, got {len(actual_keys)}"
    assert actual_keys == expected_keys, f"Keys mismatch.\nExpected: {expected_keys}\nActual: {actual_keys}"


def test_no_json_values_in_output():
    """Test that output contains no JSON values"""
    output_file = "/app/task_file/output/keys.txt"
    # Values from the input JSON that should NOT appear in output
    values_to_check = ["madireddy@test.com", "2323", "02-03-2014-13:41", "application"]
    
    with open(output_file, 'r') as f:
        content = f.read()
    
    for value in values_to_check:
        assert value not in content, f"Found JSON value in output: {value}"


def test_no_json_formatting():
    """Test that output contains no JSON formatting characters"""
    output_file = "/app/task_file/output/keys.txt"
    
    with open(output_file, 'r') as f:
        content = f.read()
    
    # Check for JSON formatting that shouldn't be there
    assert "{" not in content, "Output contains curly braces"
    assert "}" not in content, "Output contains curly braces"
    assert '"' not in content, "Output contains quotes"


def test_keys_format_one_per_line():
    """Test that each key is on its own line with no extra whitespace"""
    output_file = "/app/task_file/output/keys.txt"
    expected_keys = ["email", "versionID", "context", "date", "versionName"]
    
    with open(output_file, 'r') as f:
        lines = f.readlines()
    
    # Filter out empty lines
    non_empty_lines = [l.rstrip('\n') for l in lines if l.strip()]
    
    assert len(non_empty_lines) == len(expected_keys), f"Expected {len(expected_keys)} lines, got {len(non_empty_lines)}"
    
    for i, line in enumerate(non_empty_lines):
        # Check no leading or trailing whitespace (we already removed newline)
        assert line == line.strip(), f"Line {i+1} has extra whitespace: '{line}'"
        # Check it matches expected key
        assert line == expected_keys[i], f"Line {i+1} mismatch: expected '{expected_keys[i]}', got '{line}'"
