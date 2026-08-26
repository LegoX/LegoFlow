from pathlib import Path

import yaml

from scripts.redact_archive_config import redact


def test_redacts_secrets_without_redacting_runtime_names():
    config = {
        "repository": {"url": "https://x-access-token:ghp_abcdefghijklmnopqrstuvwxyz123456@github.com/LegoX/repo.git"},
        "runtime_info": {
            "input": {
                "llm_api": {
                    "api_key": "sk-abcdefghijklmnopqrstuvwxyz",
                    "api_base_url": "https://private-gateway.example/v1",
                    "model": "model",
                },
                "litellm_proxy": {"master_key": "local-proxy-secret"},
                "credentials": {"hf_token": "secret", "wandb_api_key": "secret"},
                "tokenizer_name": "Qwen/tokenizer",
                "max_tokens": 128,
            }
        },
    }

    result = redact(config)

    assert result["repository"]["url"] == "https://github.com/LegoX/repo.git"
    assert result["runtime_info"]["input"]["llm_api"]["api_key"] == ""
    assert result["runtime_info"]["input"]["llm_api"]["api_base_url"] == ""
    assert result["runtime_info"]["input"]["litellm_proxy"]["master_key"] == ""
    assert result["runtime_info"]["input"]["credentials"] == {"hf_token": "", "wandb_api_key": ""}
    assert result["runtime_info"]["input"]["tokenizer_name"] == "Qwen/tokenizer"
    assert result["runtime_info"]["input"]["max_tokens"] == 128


def test_redacted_yaml_contains_no_known_secret_patterns(tmp_path: Path):
    source = tmp_path / "config.yaml"
    destination = tmp_path / "archive.yaml"
    source.write_text("url: https://user:github_pat_abcdefghijklmnopqrstuvwxyz_123456@github.com/LegoX/repo.git\n", encoding="utf-8")
    destination.write_text(yaml.safe_dump(redact(yaml.safe_load(source.read_text()))), encoding="utf-8")

    archived = destination.read_text(encoding="utf-8")
    assert "github_pat_" not in archived
    assert "user:" not in archived
