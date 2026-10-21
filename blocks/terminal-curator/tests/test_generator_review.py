"""Reject the upstream candidate fallback unless its own review approved it."""

from types import SimpleNamespace
import hashlib
import json

import pytest


def review_generator(adapter, responses, codes=None):
    """Use no upstream imports or HTTP calls; preserve its unsafe fallback."""
    responses = iter(responses)
    calls = []

    class FallbackParent:
        def __init__(self):
            self.task_name = "terminal-review-fixture"
            self.candidates = [
                {
                    "test_outputs_py": code,
                    "test_sh": "#!/bin/bash\npython3 -m pytest /tests/test_outputs.py\n",
                    "metadata": "preserve the reviewed candidate",
                }
                for code in codes or ["def test_result():\n    assert 1 == 1\n"] * 3
            ]

        def _generate_tests(self, instruction, env_data, solution):
            for candidate in self.candidates:
                review = self._review_tests(
                    instruction, "task_file/input.txt", solution,
                    candidate["test_outputs_py"],
                )
                if review.get("pass"):
                    return candidate
            # The real upstream returns its first candidate after failed reviews.
            return self.candidates[0] if self.candidates else None

        def _write_files(self, instruction, env_data, test_data, solution, dockerfile, difficulty):
            for name, content in [("instruction.md", instruction), ("solution/solve.sh", solution),
                                  ("tests/test_outputs.py", test_data["test_outputs_py"])]:
                path = self.task_dir / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

    def fake_completion(prompt, system, **kwargs):
        calls.append(prompt)
        return next(responses)

    module = SimpleNamespace(
        TaskGenerator=FallbackParent,
        TEST_REVIEW_PROMPT_TEMPLATE=(
            "Instruction: {instruction}\nFiles: {env_file_list}\n"
            "Solution: {solution}\nTests: {test_code}"
        ),
        SYSTEM_PROMPT="Review only this offline fixture.",
        call_llm_api=fake_completion,
    )
    return adapter.generator_class(module)(), calls


@pytest.mark.parametrize("response", [
    None,
    False,
    "",
    "not valid JSON",
    'Review result: "pass": true',
    '{"pass": false, "issues": ["unstated output requirement"]}',
    '{"pass": "true", "issues": []}',
    '{"pass": 1}',
    '{"issues": []}',
    "true",
    "false",
    '[{"pass": true}]',
    '```json\n{"pass": "true"}\n```',
    '{"pass": true}',
    '{"pass": true, "issues": "invalid schema"}',
    '{"pass": true, "issues": [null]}',
    '{"pass": true, "issues": ["a concrete defect"]}',
    True,
    {"pass": True, "issues": []},
])
def test_unapproved_reviews_cannot_use_the_upstream_fallback(adapter, response):
    generator, calls = review_generator(adapter, [response] * 3)

    result = generator._generate_tests("Write the required output.", {}, "reference")

    assert len(calls) == 3, "The parent must reach its failed-review fallback"
    assert result is None


@pytest.mark.parametrize("response", [
    '{"pass": true, "issues": []}',
    '```json\n{"pass": true, "issues": []}\n```',
    '```\n{"pass": true, "issues": []}\n```',
])
def test_boolean_true_review_keeps_the_original_candidate(adapter, response):
    generator, calls = review_generator(adapter, [response])

    result = generator._generate_tests("Write the required output.", {}, "reference")

    assert len(calls) == 1
    assert result is generator.candidates[0]
    assert result["metadata"] == "preserve the reviewed candidate"


def test_a_later_candidate_needs_its_own_positive_review(adapter):
    first = "def test_first():\n    assert True\n"
    approved = "def test_second():\n    assert True\n"
    generator, calls = review_generator(
        adapter, ['{"pass": false, "issues": ["defect"]}', '{"pass": true, "issues": []}'], [first, approved],
    )

    result = generator._generate_tests("Write the required output.", {}, "reference")

    assert len(calls) == 2
    assert result is generator.candidates[1]
    assert result["test_outputs_py"] == approved


def test_failed_reviews_replace_an_earlier_approval_for_the_same_code(adapter):
    generator, calls = review_generator(adapter, [
        '{"pass": true, "issues": []}',
        '{"pass": false, "issues": ["defect"]}',
        None,
        '{"pass": "true"}',
        '```json\n{"pass": true, "issues": []}\n```',
    ])

    assert generator._generate_tests("Original instruction.", {}, "reference") is not None
    assert generator._generate_tests("Revised instruction.", {}, "reference") is None
    assert generator._generate_tests("Reviewed instruction.", {}, "reference") is not None
    assert len(calls) == 5


def test_an_unreviewed_parent_candidate_is_rejected(adapter):
    class UnreviewedParent:
        def _generate_tests(self, instruction, env_data, solution):
            return {"test_outputs_py": "def test_result():\n    assert True\n"}

    def unexpected_completion(*args, **kwargs):
        raise AssertionError("This fake parent never requested a review")

    generator = adapter.generator_class(SimpleNamespace(
        TaskGenerator=UnreviewedParent,
        call_llm_api=unexpected_completion,
    ))()
    assert generator._generate_tests("instruction", {}, "reference") is None


def test_generation_preserves_the_approval_bound_to_its_actual_files(adapter, tmp_path):
    generator, _ = review_generator(adapter, ['{"pass": true, "issues": []}'])
    instruction, solution = "Write the required output.\n", "#!/bin/bash\nprintf done\\n\n"
    data = generator._generate_tests(instruction, {}, solution)
    generator.task_dir = tmp_path / 'candidate'
    generator._write_files(instruction, {}, data, solution, 'FROM scratch')

    record = json.loads((generator.task_dir / 'generation-test-review.json').read_text())
    assert record['stage'] == 'generation-before-normalization'
    assert record['pass'] is True and record['issues'] == []
    assert record['sha256'] == {
        name: hashlib.sha256((generator.task_dir / name).read_bytes()).hexdigest()
        for name in ('instruction.md', 'solution/solve.sh', 'tests/test_outputs.py')
    }


@pytest.mark.parametrize('change', ['instruction', 'solution', 'tests'])
def test_changed_review_inputs_cannot_be_written_as_approved(adapter, tmp_path, change):
    generator, _ = review_generator(adapter, ['{"pass": true, "issues": []}'])
    instruction, solution = 'Required behavior.', 'reference'
    data = generator._generate_tests(instruction, {}, solution)
    generator.task_dir = tmp_path / 'must-not-be-written'
    if change == 'instruction':
        instruction += ' New requirement.'
    elif change == 'solution':
        solution += ' changed'
    else:
        data = {**data, 'test_outputs_py': data['test_outputs_py'] + '# changed\n'}
    with pytest.raises(ValueError, match='do not match an approved test review'):
        generator._write_files(instruction, {}, data, solution, 'FROM scratch')
    assert not generator.task_dir.exists()


@pytest.mark.parametrize('change', ['instruction', 'solution'])
def test_same_code_approval_does_not_cover_changed_inputs(adapter, monkeypatch, change):
    generator, _ = review_generator(adapter, ['{"pass": true, "issues": []}'])
    instruction, solution = 'Required behavior.', 'reference'
    data = generator._generate_tests(instruction, {}, solution)
    parent = type(generator).__mro__[1]
    monkeypatch.setattr(parent, '_generate_tests', lambda *args: data)
    if change == 'instruction':
        instruction += ' New requirement.'
    else:
        solution += ' changed'
    assert generator._generate_tests(instruction, {}, solution) is None
