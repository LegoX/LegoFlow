import os
import json
import pytest


class TestCSVToJSONLConversion:
    """Tests for CSV to JSONL conversion output verification"""

    @pytest.fixture
    def output_file_path(self):
        """Path to the output file"""
        return "/app/task_file/output/data.json"

    def test_output_file_exists(self, output_file_path):
        """Test that output file was created at the correct location"""
        assert os.path.exists(output_file_path), f"Output file not found at {output_file_path}"
        assert os.path.isfile(output_file_path), f"Output path is not a file"

    def test_output_file_not_empty(self, output_file_path):
        """Test that output file has content"""
        assert os.path.getsize(output_file_path) > 0, "Output file is empty"

    def test_output_directory_exists(self):
        """Test that output directory exists"""
        output_dir = "/app/task_file/output"
        assert os.path.isdir(output_dir), f"Output directory not found at {output_dir}"

    def test_valid_jsonl_format(self, output_file_path):
        """Test that output is valid JSONL (each line is valid JSON)"""
        with open(output_file_path, 'r') as f:
            content = f.read()

        lines = [line for line in content.strip().splitlines() if line.strip()]
        assert len(lines) > 0, "No valid JSON lines found in output"

        for i, line in enumerate(lines):
            try:
                json.loads(line)
            except json.JSONDecodeError as e:
                pytest.fail(f"Line {i+1} is not valid JSON: {line}. Error: {e}")

    def test_correct_number_of_objects(self, output_file_path):
        """Test that output has correct number of JSON objects (4 records)"""
        with open(output_file_path, 'r') as f:
            content = f.read()

        lines = [line for line in content.strip().splitlines() if line.strip()]
        assert len(lines) == 4, f"Expected 4 JSON objects, got {len(lines)}"

    def test_json_objects_have_required_fields(self, output_file_path):
        """Test that each JSON object has all required fields"""
        required_fields = {"FirstName", "LastName", "IDNumber", "Message"}

        with open(output_file_path, 'r') as f:
            content = f.read()

        lines = [line for line in content.strip().splitlines() if line.strip()]

        for i, line in enumerate(lines):
            obj = json.loads(line)
            for field in required_fields:
                assert field in obj, f"Line {i+1} missing required field: {field}"
            assert len(obj) == 4, f"Line {i+1} has unexpected number of fields"

    def test_no_json_array_wrapper(self, output_file_path):
        """Test that output is not wrapped in a JSON array"""
        with open(output_file_path, 'r') as f:
            content = f.read().strip()

        assert not content.startswith('['), "Output should not start with '[' (not a JSON array)"
        assert not content.startswith('{['), "Output should not start with '{['"

    def test_first_record_content(self, output_file_path):
        """Test that first record has correct data"""
        with open(output_file_path, 'r') as f:
            first_line = f.readline().strip()

        obj = json.loads(first_line)
        assert obj["FirstName"] == "John", "First record FirstName mismatch"
        assert obj["LastName"] == "Doe", "First record LastName mismatch"
        assert obj["IDNumber"] == "001", "First record IDNumber mismatch"
        assert obj["Message"] == "Message1", "First record Message mismatch"

    def test_second_record_content(self, output_file_path):
        """Test that second record has correct data"""
        with open(output_file_path, 'r') as f:
            f.readline()
            second_line = f.readline().strip()

        obj = json.loads(second_line)
        assert obj["FirstName"] == "George", "Second record FirstName mismatch"
        assert obj["LastName"] == "Washington", "Second record LastName mismatch"
        assert obj["IDNumber"] == "002", "Second record IDNumber mismatch"
        assert obj["Message"] == "Message2", "Second record Message mismatch"

    def test_third_record_content(self, output_file_path):
        """Test that third record has correct data"""
        with open(output_file_path, 'r') as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]

        obj = json.loads(lines[2])
        assert obj["FirstName"] == "Jane", "Third record FirstName mismatch"
        assert obj["LastName"] == "Smith", "Third record LastName mismatch"
        assert obj["IDNumber"] == "003", "Third record IDNumber mismatch"
        assert obj["Message"] == "Message3", "Third record Message mismatch"

    def test_fourth_record_content(self, output_file_path):
        """Test that fourth record has correct data"""
        with open(output_file_path, 'r') as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]

        obj = json.loads(lines[3])
        assert obj["FirstName"] == "Benjamin", "Fourth record FirstName mismatch"
        assert obj["LastName"] == "Franklin", "Fourth record LastName mismatch"
        assert obj["IDNumber"] == "004", "Fourth record IDNumber mismatch"
        assert obj["Message"] == "Message4", "Fourth record Message mismatch"

    def test_each_line_is_separate(self, output_file_path):
        """Test that each JSON object is on its own line"""
        with open(output_file_path, 'r') as f:
            lines = f.readlines()

        non_empty_lines = [l for l in lines if l.strip()]
        assert len(non_empty_lines) == 4, f"Expected 4 lines with content, got {len(non_empty_lines)}"

        for line in non_empty_lines:
            assert line.endswith('\n') or line == non_empty_lines[-1], "Each line should end with newline"

    def test_no_json_array_structure(self, output_file_path):
        """Test that the file does not contain a single JSON array"""
        with open(output_file_path, 'r') as f:
            first_char = f.read(1)

        assert first_char == '{', f"First character should be '{{', got '{first_char}'"

    def test_all_expected_records_present(self, output_file_path):
        """Test that all expected records are present with correct data"""
        expected_records = [
            {"FirstName": "John", "LastName": "Doe", "IDNumber": "001", "Message": "Message1"},
            {"FirstName": "George", "LastName": "Washington", "IDNumber": "002", "Message": "Message2"},
            {"FirstName": "Jane", "LastName": "Smith", "IDNumber": "003", "Message": "Message3"},
            {"FirstName": "Benjamin", "LastName": "Franklin", "IDNumber": "004", "Message": "Message4"}
        ]

        with open(output_file_path, 'r') as f:
            content = f.read()

        lines = [line for line in content.strip().splitlines() if line.strip()]
        assert len(lines) == len(expected_records), f"Record count mismatch"

        for i, expected in enumerate(expected_records):
            actual = json.loads(lines[i])
            assert actual == expected, f"Record {i+1} does not match expected data"
