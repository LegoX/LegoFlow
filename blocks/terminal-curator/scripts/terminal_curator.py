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
import os
from pathlib import Path, PurePosixPath
import re
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


def load_config(path: Path) -> dict:
    config = yaml.safe_load(path.read_text())
    if not isinstance(config, dict) or set(config) != {"meta_info", "runtime_info"}:
        raise ValueError("config requires meta_info and runtime_info only")
    inputs = config["runtime_info"]["input"]
    source = inputs["source"]
    if source["type"] not in {"stackoverflow", "json", "tasks"}:
        raise ValueError("source.type must be stackoverflow, json or tasks")
    for name, value in (("max_questions", source["max_questions"]), ("max_pages", source.get("max_pages", 1)),
                        ("workers", inputs["generation"]["workers"]), ("n_concurrent", inputs["validation"]["n_concurrent"])):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if source["type"] != "stackoverflow" and not source.get("path"):
        raise ValueError("source.path is required for json/tasks")
    for key, entry in config["runtime_info"]["output"].items():
        p = resolve(entry["path"])
        if not p.is_relative_to(ARTIFACTS) or p == ARTIFACTS:
            raise ValueError(f"output {key} must stay inside this block's artifacts/")
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


def collect_questions(source: dict, creds: dict) -> list[dict]:
    limit = source["max_questions"]
    if source["type"] == "json":
        raw = json.loads(resolve(source["path"]).read_text())
        questions = raw.get("questions", []) if isinstance(raw, dict) else raw
    else:
        questions = []
        ids = source.get("question_ids") or []
        if ids:
            ids = [int(i) for i in ids[:limit]]
            questions = so_request(creds, "/questions/" + ";".join(map(str, ids)), {})["items"]
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
        qid = int(question["question_id"])
        if qid in seen or int(question.get("score", 0)) < source.get("min_score", 0):
            continue
        if not question.get("accepted_answer_id") and not question.get("accepted_answer", {}).get("body"):
            continue
        if source["type"] == "json" and not question.get("accepted_answer", {}).get("body"):
            continue
        seen.add(qid)
        selected.append(dict(question))
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
    return [q for q in selected if q.get("accepted_answer", {}).get("body")]


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
        def _write_files(self, instruction, env_data, test_data, solution, dockerfile, difficulty="medium"):
            safe_environment(env_data)
            super()._write_files(instruction, env_data, test_data, solution, dockerfile, difficulty)

        def _review_tests(self, instruction, env_file_list, solution, test_code):
            prompt = module.TEST_REVIEW_PROMPT_TEMPLATE.format(instruction=instruction,
                env_file_list=env_file_list, solution=solution, test_code=test_code)
            prompt += "\nCompare every assertion against the written instruction, independently of the reference solution. Return pass=false for unstated log prefixes, exact output text, filenames, or source-code implementation checks. A detail appearing only in solve.sh is not a task requirement.\n"
            prompt += "An issue must identify a concrete defect. Checks explicitly required by the instruction are valid, not issues. If every check is justified and runnable, return pass=true and an empty issues list.\n"
            response = module.call_llm_api(prompt, module.SYSTEM_PROMPT, temperature=0.1,
                task_name=self.task_name, max_tokens=2048)
            review = {"pass": False, "issues": ["missing or invalid test review"]}
            if response:
                match = re.search(r"```(?:json)?\s*(.*?)```", response, re.DOTALL)
                try:
                    parsed = json.loads(match.group(1) if match else response)
                    if isinstance(parsed, dict) and type(parsed.get("pass")) is bool:
                        review = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            if not hasattr(self, "_reviewed_tests"):
                self._reviewed_tests = {}
            self._reviewed_tests[test_code] = review.get("pass") is True
            return review

        def _generate_tests(self, instruction, env_data, solution):
            data = super()._generate_tests(instruction, env_data, solution)
            if data and getattr(self, "_reviewed_tests", {}).get(data.get("test_outputs_py")) is True:
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


def generate_questions(questions: list[dict], run_dir: Path, inputs: dict, creds: dict) -> tuple[list[Path], dict]:
    with working_directory(run_dir):
        spec = importlib.util.spec_from_file_location("terminal_lego_generator", REPO / "generator/task_generator.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        for handler in logging.getLogger().handlers:
            handler.addFilter(SafeLog())
        module._config.update(api_base=creds["OPENAI_API_BASE"], api_key=creds["OPENAI_API_KEY"], model=creds["MODEL_NAME"])
        module.REQUEST_TIMEOUT = inputs["generation"].get("request_timeout_sec", 180)
        module.MAX_RETRIES = inputs["generation"].get("max_retries", 2)
        module.DOCKERFILE_PROMPT_TEMPLATE += "\nUse python:3.12-slim as base, install bash and required CLI tools, and install pytest==8.4.2 at image build time (python3 -m pip install). Runtime tests must work without network. Never COPY tests or solution into the image.\n"
        module.ENVIRONMENT_PROMPT_TEMPLATE += "\nAll directories and files must use relative task_file/... paths. Never use absolute paths or .. components.\n"
        module.SOLUTION_PROMPT_TEMPLATE += "\nHarbor executes your reference solution as /solution/solve.sh with NO command-line arguments. It is an installer/action script, separate from any executable requested by the instruction. If the task asks for a program at a path, write the full program to that /app/... path (for example using a quoted heredoc), chmod it executable, and perform any requested final-state actions. Do not return only that program's body as solve.sh. Do not derive task paths from solve.sh's own directory: /solution is separate from /app/task_file. The verifier runs after solve.sh finishes.\n"
        module.TEST_PROMPT_TEMPLATE += "\nTest only behavior and exact strings explicitly required by the instruction. Never require incidental log prefixes or implementation choices from the reference solution. If a detail is not specified, accept all behavior consistent with the instruction.\n"
        Generator = generator_class(module)

        candidates = run_dir / "candidates"
        candidates.mkdir()

        def generate(question):
            generator = Generator(module.SOQuestion.from_dict(question), candidates)
            generator.task_name = f"terminal-so-{int(question['question_id'])}"
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
            futures = [pool.submit(generate, q) for q in questions]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    generated.append(result)
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
    if any(isinstance(node, ast.Constant) and isinstance(node.value, str) and "solve.sh" in node.value
           and id(node) not in docstrings for node in ast.walk(tree)):
        raise ValueError("tests must verify state without invoking solve.sh")
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
        final_stage = re.split(r"(?im)^\s*FROM\s+.*$", dockerfile)[-1]
        users = re.findall(r"(?im)^\s*USER\s+([^\n]+)", final_stage)
        restore_user = "\nUSER " + users[-1] + "\n" if users else ""
        (destination / "environment/Dockerfile").write_text(dockerfile.rstrip() + "\n" + VERIFIER_IMAGE_SETUP + restore_user)
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


def evaluate_trials(job_dir: Path, expected: float) -> dict:
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
        if data.get("exception_info"):
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


def publish_tasks(accepted: list[Path], output: Path) -> None:
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


def run(config: dict) -> dict:
    inputs = config["runtime_info"]["input"]
    creds = credentials(inputs)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    run_dir = ARTIFACTS / "runs" / run_id
    run_dir.mkdir(parents=True)
    report = {"run_id": run_id, "started_at": now(), "status": "running", "source": inputs["source"]["type"],
        "total_candidates": 0, "verified_tasks": [], "rejected": {}, "jobs": {}, "validation": {}, "repos": repo_commits()}

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
            state("generate")
            candidates, usage = generate_questions(questions, run_dir, inputs, creds)
            report["token_usage"] = usage
        report["total_candidates"] = len(candidates)
        if not candidates:
            raise ValueError("no task candidates produced")
        state("normalize")
        normalized = run_dir / "normalized"
        normalized.mkdir()
        for candidate in candidates:
            try:
                normalize_task(candidate, normalized / candidate.name, {"run_id": run_id, "terminal_lego_commit": PIN})
            except (ValueError, SyntaxError, OSError, tomllib.TOMLDecodeError) as exc:
                report["rejected"][candidate.name] = redact(str(exc))
                if (normalized / candidate.name).exists():
                    shutil.rmtree(normalized / candidate.name)
        if not list(normalized.iterdir()):
            raise ValueError("no candidates passed normalization")
        for agent, expected in (("oracle", 1.0), ("nop", 0.0)):
            state("validate-" + agent)
            job = run_harbor(config, normalized, run_dir, agent)
            report["jobs"][agent] = str(job)
            report["validation"][agent] = evaluate_trials(job, expected)
        accepted = []
        for task in sorted(normalized.iterdir()):
            verdicts = [report["validation"][a].get(task.name, {"passed": False, "reason": "missing Harbor trial"}) for a in ("oracle", "nop")]
            if all(v["passed"] for v in verdicts):
                accepted.append(task)
            else:
                report["rejected"][task.name] = "; ".join(v["reason"] for v in verdicts if not v["passed"])
        if not accepted:
            raise ValueError("no candidates passed Oracle=1 and NOP=0; previous verified output is preserved")
        state("publish")
        pool = output_location(config["runtime_info"]["output"]["harbor_tasks_dir"]["path"])
        publish_tasks(accepted, pool)
        report["published_batch"] = str(pool.resolve())
        report["verified_tasks"] = [p.name for p in accepted]
        report["task_checksums"] = {p.name: task_digest(p) for p in accepted}
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


def archive(config_path: Path, config: dict, exit_code: int, started: str) -> None:
    archives = ARTIFACTS / "archives"
    archives.mkdir(parents=True, exist_ok=True)
    with (ARTIFACTS / "archive.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ids = [int(p.name[4:]) for p in archives.glob("run_*") if p.name[4:].isdigit()]
        run_id = f"run_{max(ids, default=0) + 1:03d}"
        dest = archives / run_id
        dest.mkdir()
        # Reuse the repository's tested redactor, keeping the exact overlay used by this run.
        subprocess.run([sys.executable, str(ROOT / "scripts/redact_archive_config.py"), str(config_path), str(dest / "config.yaml")], check=True)
        shutil.copytree(BLOCK / "scripts", dest / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        entry = {"id": run_id, "started_at": started, "completed_at": now(), "exit_code": exit_code,
            "status": "completed" if exit_code == 0 else ("interrupted" if exit_code in (130, 143) else "failed"),
            "archive": f"artifacts/archives/{run_id}/", "repos": repo_commits()}
        (dest / "metadata.yaml").write_text(yaml.safe_dump(entry, sort_keys=False))
        report = resolve(config["runtime_info"]["output"]["report"]["path"])
        if report.exists():
            data = json.loads(report.read_text())
            if datetime.fromisoformat(data["started_at"].replace("Z", "+00:00")) >= datetime.fromisoformat(started.replace("Z", "+00:00")):
                shutil.copy2(report, dest / "report.json")
        index_path = ARTIFACTS / "index.yaml"
        index = yaml.safe_load(index_path.read_text()) if index_path.exists() else {"runs": []}
        index.setdefault("runs", []).append(entry)
        index_path.write_text(yaml.safe_dump(index, sort_keys=False))
        state_path = ARTIFACTS / "state.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if exit_code:
            with (ARTIFACTS / "run.lock").open("a") as run_lock:
                try:
                    fcntl.flock(run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return  # A rejected concurrent start must not overwrite the active run's progress.
                old_state = not state.get("started_at") or datetime.fromisoformat(state["started_at"].replace("Z", "+00:00")) < datetime.fromisoformat(started.replace("Z", "+00:00"))
                if old_state:
                    write_json(state_path, {"status": entry["status"], "stage": "preflight", "started_at": started,
                        "updated_at": now(), "run_id": run_id, "verified_tasks": [], "total_candidates": 0, "rejected": {}})
                elif state.get("status") == "running":
                    write_json(state_path, {**state, "status": entry["status"], "stage": entry["status"], "updated_at": now()})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("TERMINAL_CURATOR_CONFIG", BLOCK / "config.yaml")))
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check"); p.add_argument("--offline", action="store_true")
    sub.add_parser("run")
    sub.add_parser("probe")
    sub.add_parser("dashboard")
    p = sub.add_parser("archive"); p.add_argument("--exit-code", type=int, default=0); p.add_argument("--started-at", default=now())
    args = parser.parse_args()
    config_path = resolve(str(args.config))
    try:
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
            with (ARTIFACTS / "run.lock").open("w") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError("another terminal-curator run is active") from None
                def interrupt(signum, frame):
                    raise InterruptedError("run interrupted")
                signal.signal(signal.SIGTERM, interrupt)
                pid_path = ARTIFACTS / "run.pid"
                pid_path.write_text(str(os.getpid()))
                try:
                    report = run(config)
                finally:
                    if pid_path.exists() and pid_path.read_text().strip() == str(os.getpid()):
                        pid_path.unlink()
                print(json.dumps({"run_id": report["run_id"], "verified_tasks": report["verified_tasks"]}))
        elif args.command == "archive":
            archive(config_path, config, args.exit_code, args.started_at)
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
        print("[FAIL] " + redact(str(exc)), file=sys.stderr)
        return 130 if isinstance(exc, (KeyboardInterrupt, InterruptedError)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
