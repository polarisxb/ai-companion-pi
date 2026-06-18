"""Core runtime for the AI companion internal life loop."""

from .lifecycle import LifeLoopRunner, WakeResult
from .memory import JsonMemoryStore, SemanticFirstMemoryStore
from .llm import (
    ClaudeCliClient,
    ClaudeCliError,
    ClaudeCliTimeoutError,
    ClaudeCliUnavailableError,
    DEEPSEEK_API_KEY_ENV,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_DEFAULT_MODEL,
    FakeLLMClient,
    HttpLLMError,
    LLMProviderConfigError,
    OllamaClient,
    OpenAICompatibleClient,
    SUPPORTED_LLM_PROVIDERS,
    create_llm_client,
)
from .paths import CompanionPaths
from .events import append_wake_event, load_wake_events
from .evaluator import ConservativeMemoryEvaluator, MemoryEvaluation
from .grounding import ConservativeGroundingEvaluator, GroundingEvaluation
from .provider_check import check_llm_provider
from .readiness import check_runtime_readiness
from .repair import GroundedOutputRepairer, RepairResult
from .replay import ReplayResult, ReplayRunner
from .predeploy import run_pi_predeploy_check
from .release_gate import audit_semantic_shadow_authority, run_m3_release_gate
from .final_freeze import run_m3_final_freeze
from .deploy_runtime import run_m4_deploy_check
from .m4_guard import run_m4_post_change_guard
from .m4_validation import run_m4_runtime_validation
from .m5_quality import run_m5_quality_check
from .m5_freeze import run_m5_final_freeze
from .m5_release import run_m5_quality_release_gate
from .m5_trial import run_m5_quality_trial
from .m6_manual_wake import run_m6_pi_manual_wake_trial
from .m6_preflight import run_m6_preflight_check
from .observation import run_m4_observation_check
from .wake_trial import classify_wake_trial_failure, run_m4_wake_trial
from .semantic_shadow import SemanticShadowWriter
from .secrets import load_local_secrets
from .trial_summary import build_trial_summary

__all__ = [
    "ClaudeCliClient",
    "ClaudeCliError",
    "ClaudeCliTimeoutError",
    "ClaudeCliUnavailableError",
    "CompanionPaths",
    "ConservativeGroundingEvaluator",
    "ConservativeMemoryEvaluator",
    "DEEPSEEK_API_KEY_ENV",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_DEFAULT_MODEL",
    "FakeLLMClient",
    "HttpLLMError",
    "JsonMemoryStore",
    "LLMProviderConfigError",
    "LifeLoopRunner",
    "GroundingEvaluation",
    "GroundedOutputRepairer",
    "MemoryEvaluation",
    "OllamaClient",
    "OpenAICompatibleClient",
    "SUPPORTED_LLM_PROVIDERS",
    "SemanticFirstMemoryStore",
    "WakeResult",
    "RepairResult",
    "ReplayResult",
    "ReplayRunner",
    "SemanticShadowWriter",
    "append_wake_event",
    "check_llm_provider",
    "check_runtime_readiness",
    "create_llm_client",
    "build_trial_summary",
    "load_wake_events",
    "load_local_secrets",
    "run_pi_predeploy_check",
    "run_m3_release_gate",
    "run_m3_final_freeze",
    "run_m4_deploy_check",
    "run_m4_post_change_guard",
    "run_m4_runtime_validation",
    "run_m5_quality_check",
    "run_m5_final_freeze",
    "run_m5_quality_release_gate",
    "run_m5_quality_trial",
    "run_m6_pi_manual_wake_trial",
    "run_m6_preflight_check",
    "run_m4_observation_check",
    "run_m4_wake_trial",
    "classify_wake_trial_failure",
    "audit_semantic_shadow_authority",
]
