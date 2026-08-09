from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DASHBOARD_DIR))

from export_static import WORKER_JS  # noqa: E402
from server import JobsRepo, _trial_token_summary, is_trial_dir  # noqa: E402


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class JobsRepoResultSummaryTest(unittest.TestCase):
    def test_exception_trial_is_not_double_counted_with_zero_reward(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            job_dir = jobs_dir / "job"
            write_json(
                job_dir / "result.json",
                {
                    "stats": {
                        "n_errors": 1,
                        "evals": {
                            "eval": {
                                "reward_stats": {"reward": {"0.0": ["same-trial"]}},
                                "exception_stats": {"AgentError": ["same-trial"]},
                            }
                        },
                    }
                },
            )

            summary = JobsRepo(jobs_dir).job_summary(job_dir)["analysis"]

            self.assertEqual(summary["resolved_total"], 0)
            self.assertEqual(summary["failed_total"], 1)
            self.assertEqual(summary["total"], 1)

    def test_job_result_supplies_live_summary_without_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            job_dir = jobs_dir / "job"
            write_json(
                job_dir / "result.json",
                {
                    "stats": {
                        "n_errors": 1,
                        "evals": {
                            "agent__model__dataset": {
                                "reward_stats": {
                                    "reward": {
                                        "1.0": ["resolved"],
                                        "0.0": ["unresolved"],
                                    }
                                },
                                "exception_stats": {"CancelledError": ["error"]},
                            }
                        },
                    }
                },
            )

            summary = JobsRepo(jobs_dir).job_summary(job_dir)

            self.assertFalse(summary["has_analysis"])
            self.assertEqual(summary["analysis"]["resolved_total"], 1)
            self.assertEqual(summary["analysis"]["failed_total"], 2)
            self.assertEqual(summary["analysis"]["total"], 3)
            self.assertEqual(summary["analysis"]["resolve_rate"], 33.33)
            self.assertEqual(summary["analysis"]["source"], "result.json")

    def test_trial_results_are_fallback_and_exceptions_are_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            job_dir = jobs_dir / "job"
            write_json(
                job_dir / "resolved" / "result.json",
                {"verifier_result": {"rewards": {"reward": 1.0}}},
            )
            write_json(
                job_dir / "error" / "result.json",
                {"exception_info": {"exception_type": "CancelledError"}},
            )
            (job_dir / "pending" / "verifier").mkdir(parents=True)

            repo = JobsRepo(jobs_dir)
            summary = repo.job_summary(job_dir)
            trials = {trial["trial_name"]: trial for trial in repo.list_trials("job")}

            self.assertEqual(summary["trial_count"], 3)
            self.assertEqual(summary["analysis"]["resolved_total"], 1)
            self.assertEqual(summary["analysis"]["failed_total"], 1)
            self.assertTrue(trials["resolved"]["resolved"])
            self.assertFalse(trials["error"]["resolved"])
            self.assertIsNone(trials["pending"]["resolved"])
            self.assertTrue(is_trial_dir(job_dir / "pending"))

    def test_analysis_artifacts_take_precedence_over_live_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            job_dir = jobs_dir / "job"
            write_json(
                job_dir / "result.json",
                {
                    "stats": {
                        "evals": {
                            "eval": {
                                "reward_stats": {
                                    "reward": {"1.0": ["one"], "0.0": ["two"]}
                                }
                            }
                        }
                    }
                },
            )
            write_json(
                job_dir / "analysis" / "score_comparison.json",
                {"resolved_total": 8, "failed_total": 2},
            )

            summary = JobsRepo(jobs_dir).job_summary(job_dir)

            self.assertTrue(summary["has_analysis"])
            self.assertEqual(summary["analysis"]["resolved_total"], 8)
            self.assertEqual(summary["analysis"]["failed_total"], 2)
            self.assertEqual(summary["analysis"]["resolve_rate"], 80.0)

    def test_live_job_result_invalidates_trial_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            job_dir = jobs_dir / "job"
            write_json(job_dir / "result.json", {"stats": {}})
            write_json(job_dir / "trial" / "result.json", {})
            repo = JobsRepo(jobs_dir)

            self.assertIsNone(repo.list_trials("job")[0]["resolved"])

            write_json(
                job_dir / "trial" / "result.json",
                {"verifier_result": {"rewards": {"reward": 1.0}}},
            )
            result_path = job_dir / "result.json"
            write_json(result_path, {"stats": {"n_trials": 1}})
            result_stat = result_path.stat()
            os.utime(
                result_path,
                ns=(result_stat.st_atime_ns, result_stat.st_mtime_ns + 1),
            )

            self.assertTrue(repo.list_trials("job")[0]["resolved"])


class TokenSummaryTest(unittest.TestCase):
    def test_length_finish_reason_marks_any_model_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trial_dir = Path(tmp)
            trajectory = trial_dir / "agent" / "litellm-trajectory.jsonl"
            trajectory.parent.mkdir(parents=True)
            trajectory.write_text(
                json.dumps(
                    {
                        "response_body": {
                            "usage": {"total_tokens": 262_144},
                            "choices": [{"finish_reason": "length"}],
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            summary = _trial_token_summary(trial_dir)

            self.assertEqual(summary["total_tokens"], 262_144)
            self.assertTrue(summary["hit_max_length"])


class StaticExportWorkerTest(unittest.TestCase):
    def test_missing_r2_binding_falls_back_to_static_assets(self) -> None:
        self.assertIn(
            'if (!env.TRAJECTORIES) {\n        return env.ASSETS.fetch(request);',
            WORKER_JS,
        )
        self.assertIn(
            'if (!object) {\n        return env.ASSETS.fetch(request);',
            WORKER_JS,
        )


if __name__ == "__main__":
    unittest.main()
