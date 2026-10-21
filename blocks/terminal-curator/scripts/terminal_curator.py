#!/usr/bin/env python3
"""Finite Terminal-Lego adapter. All mutable state belongs to artifacts/."""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import logging
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tomllib
import uuid
import xml.etree.ElementTree as ET

import requests
import yaml

BLOCK = Path(__file__).resolve().parents[1]
ROOT = BLOCK.parents[1]
ARTIFACTS = BLOCK / "artifacts"
REPO = BLOCK / "repos/terminal-lego"
PIN = "1a2a6048a0d568891407ed3c6a441c995f4491b0"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(text: str) -> str:
    for key, value in os.environ.items():
        if any(word in key.upper() for word in ("TOKEN", "KEY", "PASSWORD", "SECRET")) and len(value) >= 6:
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"\b(?:sk-[\w-]{12,}|gh[pousr]_[\w]+)\b", "[REDACTED]", text)
    text = re.sub(r"(?i)([?&](?:key|api_key|access_token)=)[^&\s]+", r"\1[REDACTED]", text)
    return re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", text)


class SafeLog(logging.Filter):
    def filter(self, record):
        record.msg, record.args = redact(record.getMessage()), ()
        if record.exc_info:
            import traceback
            record.msg += "\n" + redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return True


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def resolve(path: str) -> Path:
    p = Path(path).expanduser()
    return (BLOCK / p).resolve() if not p.is_absolute() else p.resolve()


def output_location(path: str) -> Path:
    """Keep the public symlink name; consumers resolve it to an immutable batch."""
    p = Path(path).expanduser()
    return Path(os.path.abspath(p if p.is_absolute() else BLOCK / p))


def repo_commits() -> dict:
    commits = {}
    for name, path in (("terminal-lego", REPO), ("harbor", ROOT / "blocks/tracer/repos/harbor")):
        if not (path / ".git").exists():
            continue  # An uninitialized gitlink must not resolve to the parent repository's HEAD.
        result = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True)
        if result.returncode == 0:
            commits[name] = result.stdout.strip()
    return commits


def task_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(file.relative_to(path).as_posix().encode() + b"\0")
        digest.update(file.read_bytes())
    return digest.hexdigest()


_publication_spec = importlib.util.spec_from_file_location("terminal_publication", BLOCK / "scripts/publication.py")
_publication = importlib.util.module_from_spec(_publication_spec)
_publication_spec.loader.exec_module(_publication)
task_tree_fingerprint = _publication.task_tree_fingerprint
task_tree_fingerprints = _publication.task_tree_fingerprints


def load_config(path: Path) -> dict:
    config = yaml.safe_load(path.read_text())
    if not isinstance(config, dict) or set(config) != {"meta_info", "runtime_info"}:
        raise ValueError("config requires meta_info and runtime_info only")
    inputs = config["runtime_info"]["input"]
    source = inputs["source"]
    if source["type"] not in {"stackoverflow", "json", "tasks"}:
        raise ValueError("source.type must be stackoverflow, json or tasks")
    validate_source_selection(source)
    for name, value in (("max_questions", source["max_questions"]), ("max_pages", source.get("max_pages", 1)),
                        ("workers", inputs["generation"]["workers"]), ("n_concurrent", inputs["validation"]["n_concurrent"])):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if source["type"] != "stackoverflow" and not source.get("path"):
        raise ValueError("source.path is required for json/tasks")
    declared_pin = config.get("meta_info", {}).get("repos", {}).get("terminal-lego", {}).get("commit_id")
    if declared_pin != PIN:
        raise ValueError("terminal-lego config pin differs from the supported adapter pin")
    for section, key, default in (("generation", "request_timeout_sec", 180),
                                   ("validation", "timeout_multiplier", 1.0),
                                   ("validation", "job_timeout_sec", 1800)):
        value = inputs[section].setdefault(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{section}.{key} must be a positive finite number")
    retries = inputs["generation"].setdefault("max_retries", 2)
    if type(retries) is not int or retries < 0:
        raise ValueError("generation.max_retries must be a nonnegative integer")
    artifacts = ARTIFACTS.resolve()
    outputs = config["runtime_info"]["output"]
    for key, entry in outputs.items():
        p = resolve(entry["path"])
        if not p.is_relative_to(artifacts) or p == artifacts:
            raise ValueError(f"output {key} must stay inside this block's artifacts/")
    pool = output_location(outputs["harbor_tasks_dir"]["path"])
    report = output_location(outputs["report"]["path"])
    registry = output_location(outputs["eval_registry"]["path"])
    if registry != pool / "registry.json":
        raise ValueError("eval_registry.path must be harbor_tasks_dir.path/registry.json")
    if report.is_dir():
        raise ValueError("report output must name a file, not an existing directory")
    if source["type"] in {"json", "tasks"}:
        source_path = resolve(source["path"])
        if report.resolve() == source_path or source["type"] == "tasks" and report.resolve().is_relative_to(source_path):
            raise ValueError("report output must not overwrite the source input")
    reserved = ("state.json", "index.yaml", "run.lock", "launch.lock", "archive.lock", "run.pid", "run.process.json",
                "runs", "env", "archives")
    versions = pool.parent / (pool.name + "-versions")
    for name, target in (("harbor_tasks_dir", pool), ("report", report)):
        resolved_parent = target.parent.resolve()
        if not resolved_parent.is_relative_to(artifacts):
            raise ValueError(f"output {name} parent must stay inside artifacts/")
        # The public pool may itself be a symlink; its parent and report cannot
        # point into an already published version, even through another symlink.
        protected_paths = (target, resolved_parent) if name == "harbor_tasks_dir" else (target, target.resolve())
        for protected in protected_paths:
            if any(part.endswith("-versions") for part in protected.relative_to(artifacts).parts):
                raise ValueError(f"output {name} must not modify immutable published versions")
            for item in reserved:
                reserved_path = artifacts / item
                if protected == reserved_path or protected.is_relative_to(reserved_path):
                    raise ValueError(f"output {name} overlaps reserved artifacts/{item}")
    for target in (report, report.resolve()):
        if (target == pool or target.is_relative_to(pool) or pool.is_relative_to(target)
                or target == versions or target.is_relative_to(versions)):
            raise ValueError("report output must not overlap the published task pool or versions")
    return config


def credentials(inputs: dict) -> dict:
    llm = inputs["llm_api"]
    so = inputs["stackoverflow"]
    base = os.environ.get("OPENAI_API_BASE_URL") or os.environ.get("OPENAI_API_BASE") or llm.get("api_base_url", "")
    base = base.rstrip("/")
    if base and not base.endswith("/v1"):
        base += "/v1"
    result = {
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY") or llm.get("api_key", ""),
        "OPENAI_API_BASE": base,
        "MODEL_NAME": os.environ.get("MODEL_NAME") or llm["model"],
        "SO_API_KEY": os.environ.get("SO_API_KEY") or so.get("api_key", ""),
        "SO_API_BASE_URL": os.environ.get("SO_API_BASE_URL") or so["api_base_url"],
    }
    # Register config-provided credentials for log filtering too. Never serialize this mapping.
    for name, value in result.items():
        if value:
            os.environ[name] = str(value)
    return result


def check(config: dict, offline: bool = False) -> list[str]:
    errors = []
    inputs = config["runtime_info"]["input"]
    source = inputs["source"]
    creds = credentials(inputs)
    if source["type"] != "tasks":
        for name in ("OPENAI_API_KEY", "OPENAI_API_BASE"):
            if not creds[name]:
                errors.append(f"missing {name}")
    if source["type"] != "stackoverflow" and not resolve(source["path"]).exists():
        errors.append("source.path does not exist")
    if repo_commits().get("terminal-lego") != PIN:
        errors.append("Terminal-Lego submodule missing or differs from pinned commit; run scripts/setup.sh")
    elif subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"], capture_output=True, text=True).stdout.strip():
        errors.append("Terminal-Lego submodule has local edits")
    if not offline:
        harbor_bin = resolve(inputs["validation"]["harbor_bin"])
        harbor_repo = ROOT / "blocks/tracer/repos/harbor"
        tracer_config = yaml.safe_load((ROOT / "blocks/tracer/config.yaml").read_text())
        harbor_pin = tracer_config["meta_info"]["repositories"]["harbor"]["commit"]
        if repo_commits().get("harbor") != harbor_pin:
            errors.append("Harbor repository differs from Tracer's supported pin")
        elif subprocess.run(["git", "-C", str(harbor_repo), "status", "--porcelain"], capture_output=True, text=True).stdout.strip():
            errors.append("Harbor repository has local edits")
        if not harbor_bin.is_file():
            errors.append("Harbor environment missing; run scripts/setup.sh")
        else:
            code = "import harbor; from pathlib import Path; print(Path(harbor.__file__).resolve())"
            result = subprocess.run([str(harbor_bin.parent / "python"), "-c", code], capture_output=True, text=True)
            if result.returncode or not result.stdout.strip().startswith(str(harbor_repo.resolve() / "src/harbor") + "/"):
                errors.append("Harbor environment must import Tracer's pinned checkout")
        if not shutil.which("docker"):
            errors.append("Docker CLI missing on execution node")
        else:
            try:
                r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=20)
                if r.returncode:
                    errors.append("Docker daemon unreachable")
                r = subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=20)
                if r.returncode:
                    errors.append("Docker Compose plugin missing")
            except subprocess.TimeoutExpired:
                errors.append("Docker preflight timed out")
    return errors


def so_request(creds: dict, endpoint: str, params: dict) -> dict:
    params = {"site": "stackoverflow", "filter": "withbody", **params}
    if creds["SO_API_KEY"]:
        params["key"] = creds["SO_API_KEY"]
    try:
        response = requests.get(creds["SO_API_BASE_URL"].rstrip("/") + endpoint, params=params, timeout=30)
        if response.status_code != 200:
            raise ValueError(f"StackExchange HTTP {response.status_code}")
        data = response.json()
    except requests.RequestException as exc:
        raise ValueError(f"StackExchange request failed ({type(exc).__name__})") from None
    if data.get("error_id"):
        raise ValueError(f"StackExchange error {data['error_id']}")
    if data.get("backoff"):
        # One finite request, then return control to the operator rather than silently sleeping indefinitely.
        raise ValueError(f"StackExchange asks for backoff {data['backoff']}s; retry this finite batch later")
    return data


def question_id(value) -> int:
    if isinstance(value, bool) or not (type(value) is int or isinstance(value, str) and re.fullmatch(r"[0-9]+", value)):
        raise ValueError("question_id must be a positive integer")
    result = int(value)
    if result <= 0:
        raise ValueError("question_id must be a positive integer")
    return result


def validate_source_selection(source: dict) -> None:
    """A scalar ID/tag must not turn into an unintended character-wise query."""
    ids = source.get("question_ids")
    if ids is not None:
        if not isinstance(ids, list):
            raise ValueError("source.question_ids must be an array of positive integer IDs")
        for value in ids:
            question_id(value)
    tags = source.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
        raise ValueError("source.tags must be an array of nonempty strings")
    if type(source.get("min_score", 0)) is not int:
        raise ValueError("source.min_score must be an integer")


def collect_questions(source: dict, creds: dict) -> list[dict]:
    validate_source_selection(source)
    limit = source["max_questions"]
    if source["type"] == "json":
        raw = json.loads(resolve(source["path"]).read_text())
        questions = raw.get("questions", []) if isinstance(raw, dict) else raw
        if not isinstance(questions, list):
            raise ValueError("JSON source questions must be an array")
    else:
        questions = []
        ids = source.get("question_ids") or []
        if ids:
            ids = list(dict.fromkeys(question_id(i) for i in ids))[:limit]
            for start in range(0, len(ids), 100):
                questions.extend(so_request(creds, "/questions/" + ";".join(map(str, ids[start:start + 100])), {}).get("items", []))
        else:
            for tag in source.get("tags", []):
                for page in range(1, source.get("max_pages", 1) + 1):
                    data = so_request(creds, "/questions", {"tagged": tag, "sort": "votes", "order": "desc",
                        "min": source.get("min_score", 10), "pagesize": min(100, max(limit * 3, 10)), "page": page})
                    questions.extend(data.get("items", []))
                    if len({q["question_id"] for q in questions if q.get("accepted_answer_id")}) >= limit or not data.get("has_more"):
                        break
                if len({q["question_id"] for q in questions if q.get("accepted_answer_id")}) >= limit:
                    break
    selected, seen = [], set()
    for question in questions:
        if not isinstance(question, dict):
            raise ValueError("each source question must be an object")
        qid = question_id(question.get("question_id"))
        if qid in seen or int(question.get("score", 0)) < source.get("min_score", 0):
            continue
        answer = question.get("accepted_answer")
        answer = answer if isinstance(answer, dict) else {}
        body = answer.get("body")
        has_body = isinstance(body, str) and bool(body.strip())
        if not question.get("accepted_answer_id") and not has_body:
            continue
        if source["type"] == "json" and not has_body:
            continue
        seen.add(qid)
        selected.append({**question, "question_id": qid})
        if len(selected) == limit:
            break
    if source["type"] == "stackoverflow":
        answer_ids = [q["accepted_answer_id"] for q in selected]
        answers = {}
        for start in range(0, len(answer_ids), 30):
            data = so_request(creds, "/answers/" + ";".join(map(str, answer_ids[start:start + 30])), {})
            answers.update({a["answer_id"]: a for a in data.get("items", [])})
        for q in selected:
            q["accepted_answer"] = answers.get(q["accepted_answer_id"], {})
    return [q for q in selected if isinstance((q.get("accepted_answer") or {}).get("body"), str)
            and q["accepted_answer"]["body"].strip()]


def safe_environment(env_data: dict) -> None:
    if not isinstance(env_data, dict) or not isinstance(env_data.get("files", {}), dict):
        raise ValueError("environment must contain a files mapping")
    paths = list(env_data.get("directories", [])) + list(env_data.get("files", {}))
    for name in paths:
        if not isinstance(name, str):
            raise ValueError("environment paths must be strings")
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or "\\" in name or not p.parts or p.parts[0] != "task_file":
            raise ValueError("generated environment paths must stay within task_file/")
    if sum(len(str(c).encode()) for c in env_data.get("files", {}).values()) > 10_000_000:
        raise ValueError("generated environment exceeds 10 MB")


def generator_class(module):
    """Keep upstream generation, but never accept its failed-review fallback."""
    class Generator(module.TaskGenerator):
        @staticmethod
        def _review_hashes(instruction, solution, test_code):
            return {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in (
                ("instruction.md", instruction), ("solution/solve.sh", solution),
                ("tests/test_outputs.py", test_code))}

        def _approved_review(self, instruction, solution, test_code):
            record = getattr(self, "_reviewed_tests", {}).get(test_code)
            if (record and record["pass"] is True and record["issues"] == []
                    and record["sha256"] == self._review_hashes(instruction, solution, test_code)):
                return record
            return None

        def _write_files(self, instruction, env_data, test_data, solution, dockerfile, difficulty="medium"):
            safe_environment(env_data)
            review = self._approved_review(instruction, solution, test_data.get("test_outputs_py", ""))
            if review is None:
                raise ValueError("generated files do not match an approved test review")
            super()._write_files(instruction, env_data, test_data, solution, dockerfile, difficulty)
            write_json(self.task_dir / "generation-test-review.json", {
                "stage": "generation-before-normalization", **review,
            })

        def _review_tests(self, instruction, env_file_list, solution, test_code):
            prompt = module.TEST_REVIEW_PROMPT_TEMPLATE.format(instruction=instruction,
                env_file_list=env_file_list, solution=solution, test_code=test_code)
            prompt += "\nCompare every assertion against the written instruction, independently of the reference solution. Return pass=false for unstated log prefixes, exact output text, filenames, or source-code implementation checks. A detail appearing only in solve.sh is not a task requirement.\n"
            prompt += "An issue must identify a concrete defect. Checks explicitly required by the instruction are valid, not issues. If every check is justified and runnable, return pass=true and an empty issues list.\n"
            prompt += "A pristine task with no learner deliverables must reach test assertions and fail normally, with zero pytest setup/collection errors. Reject fixtures that unconditionally read, copy, stat, or execute a missing learner output. Each such prerequisite belongs in a test-body assertion before use.\n"
            prompt += 'Return a JSON object with boolean "pass" and "issues" as an array of strings. An approval requires issues=[].\n'
            response = module.call_llm_api(prompt, module.SYSTEM_PROMPT, temperature=0.1,
                task_name=self.task_name, max_tokens=2048)
            review = {"pass": False, "issues": ["missing or invalid test review"]}
            if isinstance(response, str) and response:
                match = re.search(r"```(?:json)?\s*(.*?)```", response, re.DOTALL)
                try:
                    parsed = json.loads(match.group(1) if match else response)
                    if (isinstance(parsed, dict) and type(parsed.get("pass")) is bool
                            and isinstance(parsed.get("issues"), list)
                            and all(isinstance(issue, str) for issue in parsed["issues"])):
                        if parsed["pass"] and parsed["issues"]:
                            review = {"pass": False, "issues": ["inconsistent approval contains reported defects"]}
                        else:
                            review = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            if not hasattr(self, "_reviewed_tests"):
                self._reviewed_tests = {}
            self._reviewed_tests[test_code] = {
                "pass": review["pass"], "issues": list(review["issues"]),
                "sha256": self._review_hashes(instruction, solution, test_code),
            }
            return review

        def _generate_tests(self, instruction, env_data, solution):
            data = super()._generate_tests(instruction, env_data, solution)
            if data and self._approved_review(instruction, solution, data.get("test_outputs_py", "")):
                return data
            return None
    return Generator


@contextmanager
def working_directory(path: Path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def generate_questions(questions: list[dict], run_dir: Path, inputs: dict, creds: dict, progress=None) -> tuple[list[Path], dict]:
    with working_directory(run_dir):
        spec = importlib.util.spec_from_file_location("terminal_lego_generator", REPO / "generator/task_generator.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        for handler in logging.getLogger().handlers:
            handler.addFilter(SafeLog())
        module._config.update(api_base=creds["OPENAI_API_BASE"], api_key=creds["OPENAI_API_KEY"], model=creds["MODEL_NAME"])
        module.REQUEST_TIMEOUT = inputs["generation"].get("request_timeout_sec", 180)
        # Upstream names its total-attempt count MAX_RETRIES; config counts
        # retries after the initial request, so zero still makes one attempt.
        module.MAX_RETRIES = inputs["generation"].get("max_retries", 2) + 1
        module.INSTRUCTION_PROMPT_TEMPLATE += "\nReturn only the task specification: required behavior, supplied inputs, deliverables, and constraints. Do not include the reference algorithm, a solution walkthrough, chat offers, or invitations to ask follow-up questions.\n"
        module.DOCKERFILE_PROMPT_TEMPLATE += "\nUse python:3.12-slim as base, install bash and required CLI tools, and install pytest==8.4.2 at image build time (python3 -m pip install). Runtime tests must work without network. Never COPY tests or solution into the image. All generated files begin with ordinary non-executable permissions. After COPY, explicitly chmod any supplied input program that the specification says is executable. Do not require the learner to modify read-only inputs to repair the environment.\n"
        module.ENVIRONMENT_PROMPT_TEMPLATE += "\nAll directories and files must use relative task_file/... paths. Never use absolute paths or .. components.\n"
        module.SOLUTION_PROMPT_TEMPLATE += "\nHarbor executes your reference solution as /solution/solve.sh with NO command-line arguments. It is an installer/action script, separate from any executable requested by the instruction. If the task asks for a program at a path, write the full program to that /app/... path (for example using a quoted heredoc), chmod it executable, and perform any requested final-state actions. Do not return only that program's body as solve.sh. Do not derive task paths from solve.sh's own directory: /solution is separate from /app/task_file. The verifier runs after solve.sh finishes.\n"
        module.TEST_PROMPT_TEMPLATE += "\nTest only behavior and exact strings explicitly required by the instruction. Never require incidental log prefixes or implementation choices from the reference solution. If a detail is not specified, accept all behavior consistent with the instruction. A pristine task with no learner outputs must fail test-body assertions with zero setup/collection errors: do not unconditionally read, copy, stat, or execute learner outputs inside fixtures. Assert their existence inside the test body before using them. Use bounded subprocess timeouts.\n"
        Generator = generator_class(module)

        candidates = run_dir / "candidates"
        candidates.mkdir()

        def generate(question):
            generator = Generator(module.SOQuestion.from_dict(question), candidates)
            generator.task_name = f"terminal-so-{question_id(question['question_id'])}"
            generator.task_dir = candidates / generator.task_name
            if generator.generate():
                write_json(generator.task_dir / "provenance.json", {
                    "kind": "stackoverflow", "question_id": question["question_id"], "url": question.get("link"),
                    "title": question["title"], "author": question.get("owner", {}),
                    "answer_id": question.get("accepted_answer_id"),
                    "answer_author": question.get("accepted_answer", {}).get("owner", {}),
                    "content_license": question.get("content_license", "see StackOverflow source"),
                    "terminal_lego_commit": PIN, "model": creds["MODEL_NAME"],
                })
                return generator.task_dir
            return None

        generated = []
        with ThreadPoolExecutor(max_workers=inputs["generation"]["workers"]) as pool:
            futures = {pool.submit(generate, q): index for index, q in enumerate(questions)}
            for future in as_completed(futures):
                index = futures[future]
                task_id = f"terminal-so-{question_id(questions[index]['question_id'])}"
                reason = ""
                try:
                    result = future.result()
                    if result is None:
                        reason = "generator returned no complete, approved candidate"
                except Exception as exc:
                    result = None
                    reason = redact(f"generation failed ({type(exc).__name__}): {exc}")
                if result:
                    generated.append(result)
                if progress is not None:
                    progress({"task_id": task_id, "status": "generated" if result else "failed", "reason": reason})
        return sorted(generated), module.token_tracker.get_summary()


VERIFIER = '''#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
rm -f /logs/verifier/reward.txt /logs/verifier/junit.xml
PYTHONPATH="/opt/terminal-curator-verifier${PYTHONPATH:+:$PYTHONPATH}" python3 -m pytest /tests/test_outputs.py -q -rA --junitxml=/logs/verifier/junit.xml
rc=$?
if [ "$rc" -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
exit "$rc"
'''

VERIFIER_IMAGE_SETUP = '''
# LegoFlow verifier runtime: install during build, never during grading.
USER root
RUN if ! python3 -m pip --version >/dev/null 2>&1; then \\
      if command -v apt-get >/dev/null 2>&1; then \\
        apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 python3-pip && rm -rf /var/lib/apt/lists/*; \\
      elif command -v apk >/dev/null 2>&1; then apk add --no-cache python3 py3-pip; \\
      elif command -v dnf >/dev/null 2>&1; then dnf install -y python3 python3-pip && dnf clean all; \\
      elif command -v yum >/dev/null 2>&1; then yum install -y python3 python3-pip && yum clean all; \\
      else echo 'A Python 3 and pip capable image is required for the terminal verifier' >&2; exit 1; fi; \\
    fi
RUN python3 -m pip install --no-cache-dir --target=/opt/terminal-curator-verifier pytest==8.4.2
'''


def dockerfile_final_user(dockerfile: str) -> str | None:
    """Resolve explicit USER and inheritance from stages declared in this file.

    None means an external base's USER is unknown. Preserve it rather than
    silently replacing an inherited nonroot runtime user with root.
    """
    stages = {}
    current_user = None
    current_stage = None
    pending = ""
    for line in dockerfile.splitlines():
        if not pending and (not line.strip() or line.lstrip().startswith("#")):
            continue
        pending += line.strip()
        if pending.endswith("\\"):
            pending = pending[:-1] + " "
            continue
        instruction, pending = pending, ""
        if re.match(r"(?i)^FROM\s", instruction):
            words = shlex.split(instruction)
            bases = [word for word in words[1:] if not word.startswith("--")]
            if not bases:
                continue
            current_user = stages.get(bases[0].lower())
            current_stage = bases[2].lower() if len(bases) >= 3 and bases[1].upper() == "AS" else None
            if current_stage:
                stages[current_stage] = current_user
        elif re.match(r"(?i)^USER\s", instruction):
            current_user = instruction.split(None, 1)[1]
            if current_stage:
                stages[current_stage] = current_user
    return current_user


def normalize_task(source: Path, destination: Path, provenance: dict) -> Path:
    import tomli_w
    if source.is_symlink() or any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("candidate symlinks are not supported")
    for name in ("instruction.md", "task.toml", "environment/Dockerfile", "solution/solve.sh", "tests/test_outputs.py"):
        if not (source / name).is_file() or not (source / name).stat().st_size:
            raise ValueError(f"missing or empty required file: {name}")
    tree = ast.parse((source / "tests/test_outputs.py").read_text())
    if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_") for n in ast.walk(tree)):
        raise ValueError("test_outputs.py contains no pytest tests")
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    if any(isinstance(node, ast.Constant) and isinstance(node.value, str)
           and re.search(r"(?<![\w/.-])/solution(?:/|$|[^\w./-])", node.value)
           and id(node) not in docstrings for node in ast.walk(tree)):
        raise ValueError("tests must verify state without accessing /solution, including the reference solve.sh")
    config = tomllib.loads((source / "task.toml").read_text())
    config.pop("version", None)
    config["schema_version"] = "1.0"
    env = config.setdefault("environment", {})
    env["build_timeout_sec"] = max(float(env.get("build_timeout_sec", 300)), 300)
    for legacy, modern, default in (("memory", "memory_mb", 1024), ("storage", "storage_mb", 5120)):
        value = env.pop(legacy, None)
        if value is not None and modern not in env:
            match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMGTP]?)B?", str(value).strip(), re.I)
            if not match:
                raise ValueError(f"unsupported {legacy} limit")
            env[modern] = max(1, int(float(match[1]) * {"": 1 / 1024**2, "K": 1 / 1024, "M": 1, "G": 1024, "T": 1024**2, "P": 1024**3}[match[2].upper()]))
        env.setdefault(modern, default)
    # Task secrets and host environment interpolation have no place in a generated dataset.
    for section in ("environment", "agent", "verifier", "solution"):
        if config.get(section, {}).get("env"):
            raise ValueError("candidate task declares environment variables; remove them before validation")
    dockerfile = (source / "environment/Dockerfile").read_text()
    if re.search(r"(?im)^\s*(?:COPY|ADD)\b[^\n]*(?:solution|/tests|\.\./)", dockerfile):
        raise ValueError("Docker build context must not include solutions or tests")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    (destination / "task.toml").write_text(tomli_w.dumps(config))
    if "# LegoFlow verifier runtime:" not in dockerfile:
        final_user = dockerfile_final_user(dockerfile)
        setup = VERIFIER_IMAGE_SETUP if final_user is not None else VERIFIER_IMAGE_SETUP.replace("\nUSER root\n", "\n")
        restore_user = "\nUSER " + final_user + "\n" if final_user is not None else ""
        (destination / "environment/Dockerfile").write_text(dockerfile.rstrip() + "\n" + setup + restore_user)
    upstream = destination / "tests/test.sh"
    if upstream.exists() and not upstream.with_name("test.sh.upstream").exists():
        upstream.rename(upstream.with_name("test.sh.upstream"))
    upstream.write_text(VERIFIER)
    upstream.chmod(0o755)
    solve = destination / "solution/solve.sh"
    solution = solve.read_text()
    if not solution.startswith("#!"):
        solution = "#!/bin/bash\n" + solution
    solve.write_text(solution)
    solve.chmod(0o755)
    current = json.loads((source / "provenance.json").read_text()) if (source / "provenance.json").exists() else {}
    write_json(destination / "provenance.json", {**current, **provenance})
    return destination


def evaluate_trials(job_dir: Path, expected: float, expected_checksums: dict | None = None) -> dict:
    results = {}
    for result_path in sorted(job_dir.glob("*/result.json")):
        data = json.loads(result_path.read_text())
        task = data.get("task_name", "")
        if not task:
            continue
        reason = ""
        trial = result_path.parent
        if task in results:
            results[task] = {"passed": False, "reason": "duplicate trial results", "trial_path": str(trial)}
            continue
        if expected_checksums is not None and task not in expected_checksums:
            reason = "unexpected task in Harbor results"
        elif expected_checksums is not None and data.get("task_checksum") != expected_checksums[task]:
            reason = "Harbor task checksum differs from the validated candidate"
        elif data.get("exception_info"):
            reason = "Harbor trial exception"
        elif not isinstance((data.get("verifier_result") or {}).get("rewards", {}).get("reward"), (int, float)) or isinstance((data.get("verifier_result") or {}).get("rewards", {}).get("reward"), bool) or (data.get("verifier_result") or {}).get("rewards", {}).get("reward") != expected:
            reason = f"expected reward {expected}"
        else:
            try:
                root = ET.parse(trial / "verifier/junit.xml").getroot()
                suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
                tests = sum(int(s.get("tests", 0)) for s in suites)
                errors = sum(int(s.get("errors", 0)) for s in suites)
                failures = sum(int(s.get("failures", 0)) for s in suites)
                skipped = sum(int(s.get("skipped", 0)) for s in suites)
                if tests <= skipped or errors or (expected == 1 and failures) or (expected == 0 and not failures):
                    reason = "pytest did not execute meaningful assertions with the expected result"
            except (FileNotFoundError, ET.ParseError, ValueError):
                reason = "missing or invalid pytest JUnit report"
            exit_file = trial / "agent/exit-code.txt"
            if expected == 1 and exit_file.exists() and exit_file.read_text().strip() != "0":
                reason = "reference solution exited nonzero"
        results[task] = {"passed": not reason, "reason": reason, "trial_path": str(trial)}
    return results


def publish_tasks(accepted: list[Path], output: Path, validate_batch=None) -> None:
    if not accepted or len({p.name for p in accepted}) != len(accepted):
        raise ValueError("publication requires distinct verified task IDs")
    output.parent.mkdir(parents=True, exist_ok=True)
    versions = output.parent / (output.name + "-versions")
    versions.mkdir(exist_ok=True)
    batch = versions / uuid.uuid4().hex
    batch.mkdir()
    staging = output.with_name("." + output.name + "." + uuid.uuid4().hex)
    published = False
    try:
        for candidate in accepted:
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", candidate.name) or candidate.is_symlink() or any(p.is_symlink() for p in candidate.rglob("*")):
                raise ValueError("unsafe publication task ID or symlink")
            shutil.copytree(candidate, batch / candidate.name)
        (batch / "verifiable_tasks.txt").write_text("".join(p.name + "\n" for p in sorted(accepted)))
        write_json(batch / "registry.json", [{"name": "terminal-curator", "version": "1.0",
            "description": "Terminal tasks verified with Harbor Oracle=1 and NOP=0.",
            "tasks": [{"name": candidate.name, "path": str((batch / candidate.name).resolve())}
                      for candidate in sorted(accepted)]}])
        if validate_batch is not None:
            validate_batch(batch)  # Bind the actual copied bytes before switching the public pointer.
        if output.exists() and not output.is_symlink():
            raise ValueError("existing output is not a versioned batch symlink; choose an empty output path")
        staging.symlink_to(batch.relative_to(output.parent), target_is_directory=True)
        staging.rename(output)
        published = True
    finally:
        if staging.is_symlink():
            staging.unlink()
        if not published:
            shutil.rmtree(batch)


def harbor_task_checksums(config: dict, candidates: Path) -> dict:
    """Use the same pinned Harbor Task implementation as validation/consumers."""
    harbor = resolve(config["runtime_info"]["input"]["validation"]["harbor_bin"])
    script = '''from pathlib import Path
import json, sys
from harbor.models.task.task import Task
root = Path(sys.argv[1])
tasks = sorted(path for path in root.iterdir() if path.is_dir())
if not tasks or any(path.is_symlink() or any(p.is_symlink() for p in path.rglob("*")) for path in tasks):
    raise ValueError("checksum input must contain real, non-symlink candidate directories")
print(json.dumps({path.name: Task(path).checksum for path in tasks}))
'''
    result = subprocess.run([str(harbor.parent / "python"), "-c", script, str(candidates)],
                            check=True, capture_output=True, text=True,
                            timeout=config["runtime_info"]["input"]["validation"].get("job_timeout_sec", 1800))
    checksums = json.loads(result.stdout)
    if not isinstance(checksums, dict) or not checksums or any(
            not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value) for value in checksums.values()):
        raise ValueError("Harbor checksum output is invalid")
    return checksums


def run_harbor(config: dict, candidates: Path, run_dir: Path, agent: str) -> Path:
    validation = config["runtime_info"]["input"]["validation"]
    harbor = resolve(validation["harbor_bin"])
    schema = "from pathlib import Path; import sys; from harbor.models.task.config import TaskConfig; [TaskConfig.model_validate_toml(p.read_text()) for p in Path(sys.argv[1]).glob('*/task.toml')]"
    subprocess.run([str(harbor.parent / "python"), "-c", schema, str(candidates)], check=True, capture_output=True, text=True)
    job_name = agent
    jobs_dir = run_dir / "validation"
    command = [str(harbor), "run", "--path", str(candidates), "--agent", agent,
        "--jobs-dir", str(jobs_dir), "--job-name", job_name, "--n-concurrent", str(validation["n_concurrent"]),
        "--timeout-multiplier", str(validation["timeout_multiplier"]), "--max-retries", "0", "--delete"]
    # Oracle/NOP validation must never inherit LLM credentials, proxy keys, or task environment overrides.
    child_env = {k: v for k, v in os.environ.items() if not any(t in k.upper() for t in ("API_KEY", "TOKEN", "PASSWORD", "SECRET"))}
    with (run_dir / f"{agent}.log").open("w") as log:
        child = subprocess.Popen(command, env=child_env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = child.wait(timeout=validation.get("job_timeout_sec", 1800))
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
    if rc:
        raise ValueError(f"Harbor {agent} job exited {rc}; see local {agent}.log")
    return jobs_dir / job_name


def run(config: dict, run_id: str | None = None, config_path: Path | None = None) -> dict:
    inputs = config["runtime_info"]["input"]
    creds = credentials(inputs)
    run_id = validate_run_id(run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", run_id):
        raise ValueError("invalid run ID")
    run_dir = ARTIFACTS / "runs" / run_id
    run_dir.mkdir(parents=True)
    snapshot_run_inputs(config, run_dir, config_path)
    report = {"run_id": run_id, "started_at": now(), "status": "running", "source": inputs["source"]["type"],
        "total_candidates": 0, "collected_questions": None, "generation_completed": 0, "generation_failed": 0,
        "candidate_ids": [], "task_progress": {}, "verified_tasks": [], "rejected": {}, "jobs": {}, "validation": {}, "repos": repo_commits()}

    def state(stage):
        write_json(ARTIFACTS / "state.json", {**report, "stage": stage, "updated_at": now()})

    try:
        state("check")
        errors = check(config)
        if errors:
            raise ValueError("; ".join(errors))
        state("collect")
        if inputs["source"]["type"] == "tasks":
            source_dir = resolve(inputs["source"]["path"])
            candidates = [p for p in sorted(source_dir.iterdir()) if p.is_dir()][:inputs["source"]["max_questions"]]
        else:
            questions = collect_questions(inputs["source"], creds)
            write_json(run_dir / "questions.json", {"questions": questions})
            if not questions:
                raise ValueError("no accepted-answer seeds matched this batch")
            report["collected_questions"] = len(questions)
            report["task_progress"] = {f"terminal-so-{question_id(q['question_id'])}": {"stage": "generate", "status": "pending"} for q in questions}
            state("generate")
            def generation_progress(event):
                task_id = event["task_id"]
                report["generation_completed"] += 1
                failed = event["status"] == "failed"
                if failed:
                    report["generation_failed"] += 1
                    report["rejected"][task_id] = event["reason"]
                else:
                    report["candidate_ids"].append(task_id)
                    report["total_candidates"] += 1
                report["task_progress"][task_id] = {"stage": "generate", "status": "failed" if failed else "completed"}
                state("generate")
            candidates, usage = generate_questions(questions, run_dir, inputs, creds, progress=generation_progress)
            report["token_usage"] = usage
        report["total_candidates"] = len(candidates)
        report["candidate_ids"] = sorted(candidate.name for candidate in candidates)
        if not candidates:
            raise ValueError("no task candidates produced")
        state("normalize")
        normalized = run_dir / "normalized"
        normalized.mkdir()
        for candidate in candidates:
            report["task_progress"][candidate.name] = {"stage": "normalize", "status": "running"}
            state("normalize")
            try:
                normalize_task(candidate, normalized / candidate.name, {"run_id": run_id, "terminal_lego_commit": PIN})
                report["task_progress"][candidate.name]["status"] = "completed"
            except (ValueError, SyntaxError, OSError, tomllib.TOMLDecodeError) as exc:
                report["rejected"][candidate.name] = redact(str(exc))
                report["task_progress"][candidate.name]["status"] = "failed"
                if (normalized / candidate.name).exists():
                    shutil.rmtree(normalized / candidate.name)
            state("normalize")
        if not list(normalized.iterdir()):
            raise ValueError("no candidates passed normalization")
        fingerprints = task_tree_fingerprints(normalized)
        report["tree_fingerprints"] = fingerprints
        checksums = harbor_task_checksums(config, normalized)
        report["harbor_task_checksums"] = checksums
        for agent, expected in (("oracle", 1.0), ("nop", 0.0)):
            if harbor_task_checksums(config, normalized) != checksums:
                raise ValueError("normalized tasks changed before Harbor validation")
            if task_tree_fingerprints(normalized) != fingerprints:
                raise ValueError("normalized task tree changed before Harbor validation")
            for task in checksums:
                report["task_progress"][task] = {"stage": "validate-" + agent, "status": "running"}
            state("validate-" + agent)
            job = run_harbor(config, normalized, run_dir, agent)
            report["jobs"][agent] = str(job)
            report["validation"][agent] = evaluate_trials(job, expected, checksums)
            for task in checksums:
                verdict = report["validation"][agent].get(task, {})
                report["task_progress"][task] = {"stage": "validate-" + agent, "status": "completed" if verdict.get("passed") else "failed"}
            state("validate-" + agent)
            if harbor_task_checksums(config, normalized) != checksums:
                raise ValueError("normalized tasks changed during Harbor validation")
            if task_tree_fingerprints(normalized) != fingerprints:
                raise ValueError("normalized task tree changed during Harbor validation")
        accepted = []
        for task in sorted(normalized.iterdir()):
            verdicts = [report["validation"][a].get(task.name, {"passed": False, "reason": "missing Harbor trial"}) for a in ("oracle", "nop")]
            if all(v["passed"] for v in verdicts):
                accepted.append(task)
            else:
                report["rejected"][task.name] = "; ".join(v["reason"] for v in verdicts if not v["passed"])
                report["task_progress"][task.name]["status"] = "failed"
        if not accepted:
            raise ValueError("no candidates passed Oracle=1 and NOP=0; previous verified output is preserved")
        state("publish")
        pool = output_location(config["runtime_info"]["output"]["harbor_tasks_dir"]["path"])
        accepted_checksums = {task.name: checksums[task.name] for task in accepted}
        block_digests = {task.name: task_digest(task) for task in accepted}
        def validate_batch(batch):
            if harbor_task_checksums(config, batch) != accepted_checksums:
                raise ValueError("publication bytes differ from the validated candidates")
            if task_tree_fingerprints(batch) != {task.name: fingerprints[task.name] for task in accepted}:
                raise ValueError("publication tree differs from the validated candidates")
            write_json(batch / "validation.json", {
                "schema_version": 1, "run_id": run_id,
                "verified_tasks": sorted(accepted_checksums),
                "harbor_task_checksums": accepted_checksums,
                "tree_fingerprints": {name: fingerprints[name] for name in sorted(accepted_checksums)},
                "validation": {"oracle": {"passed": True, "reward": 1}, "nop": {"passed": True, "reward": 0}},
            })
        publish_tasks(accepted, pool, validate_batch=validate_batch)
        report["published_batch"] = str(pool.resolve())
        report["verified_tasks"] = [p.name for p in accepted]
        for task in accepted:
            report["task_progress"][task.name] = {"stage": "publish", "status": "completed"}
        report["task_checksums"] = block_digests
        report["status"] = "completed"
    except BaseException as exc:
        report["status"] = "interrupted" if isinstance(exc, (KeyboardInterrupt, InterruptedError)) else "failed"
        report["error"] = redact(str(exc))
        raise
    finally:
        report["completed_at"] = now()
        write_json(run_dir / "report.json", report)
        write_json(resolve(config["runtime_info"]["output"]["report"]["path"]), report)
        state(report["status"])
    return report


def write_yaml(path: Path, value) -> None:
    """Readers see either the old complete document or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with os.fdopen(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as stream:
        yaml.safe_dump(value, stream, sort_keys=False, allow_unicode=True)
    temporary.replace(path)


def snapshot_config(config: dict, path: Path) -> None:
    """Freeze the effective in-memory configuration without persisting secrets."""
    spec = importlib.util.spec_from_file_location("terminal_archive_redactor", ROOT / "scripts/redact_archive_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    write_yaml(path, module.redact(config))


def snapshot_scripts(source: Path, destination: Path, config_path: Path | None = None) -> None:
    """Archive code and dependency lists, never private profiles or symlink targets."""
    destination.mkdir(parents=True, exist_ok=True)
    excluded = config_path.resolve() if config_path is not None else None
    if not source.is_dir():
        return
    for directory, directories, files in os.walk(source, followlinks=False):
        current = Path(directory)
        directories[:] = [name for name in directories if not name.startswith('.')
                          and name != '__pycache__' and not (current / name).is_symlink()]
        for name in files:
            path = current / name
            allowed = path.suffix in {'.py', '.sh'} or (name.startswith('requirements') and path.suffix == '.txt')
            if (not allowed or name.startswith('.') or '.local.' in name or path.is_symlink()
                    or not path.is_file() or path.resolve() == excluded):
                continue
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def snapshot_run_inputs(config: dict, run_dir: Path, config_path: Path | None = None) -> None:
    snapshot_config(config, run_dir / 'config.yaml')
    snapshot_scripts(BLOCK / 'scripts', run_dir / 'scripts', config_path)


def process_start(pid: int) -> str | None:
    try:
        # comm is parenthesized and may itself contain spaces.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (FileNotFoundError, IndexError, ProcessLookupError):
        return None


def validate_run_id(run_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", run_id):
        raise ValueError("invalid terminal-curator run ID")
    return run_id


def archive(config_path: Path, config: dict | None, exit_code: int, started: str,
            run_id: str | None = None) -> None:
    # A launcher supplies its own ID, so another completed run cannot be adopted
    # while this EXIT trap waits for the archive lock.
    if run_id is not None:
        validate_run_id(run_id)
    source = ARTIFACTS / "runs" / run_id if run_id else None
    archives = ARTIFACTS / "archives"
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with (ARTIFACTS / "archive.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        archives.mkdir(parents=True, exist_ok=True)
        report = None
        if source is not None and (source / "report.json").is_file():
            candidate = json.loads((source / "report.json").read_text())
            if isinstance(candidate, dict) and candidate.get("run_id") == run_id:
                report = candidate
        index_path = ARTIFACTS / "index.yaml"
        index = yaml.safe_load(index_path.read_text()) if index_path.exists() else {"runs": []}
        if not isinstance(index, dict) or not isinstance(index.get("runs"), list):
            raise ValueError("invalid run index; preserve and repair it before archiving")
        ids = [int(p.name[4:]) for p in archives.glob("run_*") if p.name[4:].isdigit()]
        ids.extend(int(item["id"][4:]) for item in index["runs"]
                   if isinstance(item, dict) and re.fullmatch(r"run_\d+", str(item.get("id", ""))))
        archive_id = f"run_{max(ids, default=0) + 1:03d}"
        dest = archives / archive_id
        dest.mkdir()
        frozen = source / "config.yaml" if source is not None else None
        if frozen is not None and frozen.is_file():
            shutil.copy2(frozen, dest / "config.yaml")
        elif config is not None:
            snapshot_config(config, dest / "config.yaml")
        frozen_scripts = source / "scripts" if source is not None else None
        if frozen_scripts is not None and frozen_scripts.is_dir():
            shutil.copytree(frozen_scripts, dest / "scripts")
        else:
            snapshot_scripts(BLOCK / "scripts", dest / "scripts", config_path)
        entry = {"id": archive_id, "run_id": run_id, "started_at": started, "completed_at": now(), "exit_code": exit_code,
            "status": "completed" if exit_code == 0 else ("interrupted" if exit_code in (130, 143) else "failed"),
            "archive": f"artifacts/archives/{archive_id}/", "repos": report.get("repos", {}) if report else repo_commits(),
            "config_snapshot": "available" if (dest / "config.yaml").exists() else "unavailable",
            "scripts_snapshot": "run-start" if frozen_scripts is not None and frozen_scripts.is_dir() else "archive-time"}
        if report is not None:
            write_json(dest / "report.json", report)
            entry["verified_count"] = len(report.get("verified_tasks", []))
            entry["rejected_count"] = len(report.get("rejected", {}))
        write_yaml(dest / "metadata.yaml", entry)
        index["runs"].append(entry)
        write_yaml(index_path, index)
        state_path = ARTIFACTS / "state.json"
        if exit_code:
            with (ARTIFACTS / "run.lock").open("a") as run_lock:
                try:
                    fcntl.flock(run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return  # A rejected concurrent start must not overwrite the active run's progress.
                state = json.loads(state_path.read_text()) if state_path.exists() else {}
                old_state = not state.get("started_at") or datetime.fromisoformat(state["started_at"].replace("Z", "+00:00")) < datetime.fromisoformat(started.replace("Z", "+00:00"))
                if old_state:
                    write_json(state_path, {"status": entry["status"], "stage": "preflight", "started_at": started,
                        "updated_at": now(), "run_id": run_id or archive_id, "verified_tasks": [], "total_candidates": 0, "rejected": {}})
                elif run_id is not None and state.get("run_id") == run_id and state.get("status") == "running":
                    write_json(state_path, {**state, "status": entry["status"], "stage": entry["status"], "updated_at": now()})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("TERMINAL_CURATOR_CONFIG", BLOCK / "config.yaml")))
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check"); p.add_argument("--offline", action="store_true")
    p = sub.add_parser("run"); p.add_argument("--run-id")
    sub.add_parser("probe")
    sub.add_parser("dashboard")
    p = sub.add_parser("archive"); p.add_argument("--exit-code", type=int, default=0); p.add_argument("--started-at", default=now()); p.add_argument("--run-id")
    args = parser.parse_args()
    config_path = resolve(str(args.config))
    try:
        if args.command == "archive":
            # A malformed preflight config still needs a failed-run record. Do
            # not echo YAML parser source excerpts, which can contain credentials.
            try:
                config = yaml.safe_load(config_path.read_text())
                if not isinstance(config, dict):
                    config = None
            except (OSError, yaml.YAMLError):
                config = None
            archive(config_path, config, args.exit_code, args.started_at, args.run_id)
            return 0
        config = load_config(config_path)
        if args.command == "check":
            errors = check(config, args.offline)
            for error in errors:
                print("[FAIL] " + error)
            if not errors:
                print("[OK] terminal-curator config, input and runtime prerequisites")
            return 1 if errors else 0
        if args.command == "run":
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            with (ARTIFACTS / "run.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError("another terminal-curator run is active") from None
                def interrupt(signum, frame):
                    raise InterruptedError("run interrupted")
                signal.signal(signal.SIGTERM, interrupt)
                signal.signal(signal.SIGINT, interrupt)
                run_id = validate_run_id(args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
                pid_path = ARTIFACTS / "run.pid"
                process_path = ARTIFACTS / "run.process.json"
                write_json(process_path, {"pid": os.getpid(), "start_ticks": process_start(os.getpid()), "run_id": run_id})
                pid_path.write_text(str(os.getpid()))
                try:
                    report = run(config, run_id=run_id, config_path=config_path)
                finally:
                    if pid_path.exists() and pid_path.read_text().strip() == str(os.getpid()):
                        pid_path.unlink()
                    if process_path.exists() and json.loads(process_path.read_text()).get("pid") == os.getpid():
                        process_path.unlink()
                print(json.dumps({"run_id": report["run_id"], "verified_tasks": report["verified_tasks"]}))
        elif args.command == "dashboard":
            subprocess.run([sys.executable, str(BLOCK / "dashboard/progress_monitor.py"), "--config", str(config_path)], check=True)
        elif args.command == "probe":
            creds = credentials(config["runtime_info"]["input"])
            data = so_request(creds, "/info", {})
            print(f"[OK] StackOverflow quota_remaining={data.get('quota_remaining')}")
            response = requests.post(creds["OPENAI_API_BASE"] + "/chat/completions", headers={"Authorization": "Bearer " + creds["OPENAI_API_KEY"]},
                json={"model": creds["MODEL_NAME"], "messages": [{"role": "user", "content": "Reply OK"}], "max_tokens": 32}, timeout=60)
            if response.status_code != 200 or not response.json().get("choices"):
                raise ValueError(f"LLM probe failed (HTTP {response.status_code})")
            print("[OK] LLM chat completion returned a choice")
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        message = "invalid YAML configuration (source text withheld)" if isinstance(exc, yaml.YAMLError) else redact(str(exc))
        print("[FAIL] " + message, file=sys.stderr)
        return 130 if isinstance(exc, (KeyboardInterrupt, InterruptedError)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
