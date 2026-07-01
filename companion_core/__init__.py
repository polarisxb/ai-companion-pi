"""Core runtime for the AI companion internal life loop."""

from .lifecycle import LifeLoopRunner, WakeResult
from .dialogue import (
    DialogueEngine,
    DialogueResult,
    DialogueRunner,
    DialogueTurnResult,
    build_memory_proposals,
    load_dialogue_context,
    parse_dialogue_output,
)
from .m7_dialogue_freeze import (
    M7DialogueFreezeResult,
    run_m7_dialogue_freeze_check,
    write_m7_dialogue_freeze_report,
)
from .m7_memory_gate import (
    M7MemoryProposalGateResult,
    run_m7_memory_proposal_gate,
    write_m7_memory_proposal_report,
)
from .m8_memory_schema import (
    ALLOWED_MEMORY_AUTHORITIES,
    ALLOWED_MEMORY_DECISIONS,
    ALLOWED_MEMORY_RISKS,
    ALLOWED_MEMORY_TYPES,
    MEMORY_DECISION_SCHEMA,
    MemoryDecision,
    MemoryDecisionValidationError,
    append_memory_decision,
    append_memory_decisions,
    load_memory_decisions,
    normalize_memory_decision,
    validate_memory_decision,
)
from .memory_steward import (
    M8MemoryStewardResult,
    run_m8_memory_steward_readonly,
    write_m8_memory_steward_report,
)
from .m8_memory_policy import (
    M8MemoryPolicyLedgerResult,
    run_m8_memory_policy_ledger,
    write_m8_memory_policy_ledger_report,
)
from .memory_retrieval import (
    MemoryRetrievalResult,
    RetrievedMemory,
    assemble_dialogue_memory_context,
    run_m8_memory_retrieval_check,
    write_m8_memory_retrieval_report,
)
from .m8_dialogue_humanity import (
    M8DialogueHumanityResult,
    run_m8_dialogue_humanity_regression,
    write_m8_dialogue_humanity_report,
)
from .m8_memory_review import (
    M8MemoryReviewQueueResult,
    MemoryReviewError,
    approve_memory_review_decision,
    archive_memory_review_decision,
    load_memory_review_actions,
    load_memory_review_queue,
    reject_memory_review_decision,
    run_m8_memory_review_queue_check,
    write_m8_memory_review_queue_report,
)
from .m8_memory_freeze import (
    M8MemoryFreezeResult,
    run_m8_memory_freeze_check,
    write_m8_memory_freeze_report,
)
from .m9_scheduler_revalidation import (
    M9SchedulerRevalidationResult,
    discover_m9_scheduler_inventory,
    run_m9_scheduler_revalidation_check,
    source_only_m9_scheduler_inventory,
    write_m9_scheduler_revalidation_report,
)
from .m9_scheduler_dry_run import (
    M9SchedulerDryRunResult,
    append_scheduler_attempts,
    load_scheduler_attempts,
    run_m9_scheduler_dry_run,
    write_m9_scheduler_dry_run_report,
)
from .m9_scheduler_activation import (
    M9SchedulerActivationResult,
    build_m9_cron_line,
    disable_command,
    enable_command,
    run_m9_scheduler_activation,
    run_m9_scheduler_disable,
    write_m9_scheduler_activation_report,
)
from .m9_scheduler_tick import (
    M9SchedulerTickResult,
    WakeCommandResult,
    initialize_scheduler_presence_state,
    run_m9_scheduler_tick,
)
from .m9_presence_observation import (
    M9PresenceObservationResult,
    run_m9_presence_observation,
    write_m9_presence_observation_report,
)
from .dialogue_replay import DialogueReplayCheckResult, check_dialogue_transcript
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
from .m6_observation import run_m6_pi_observation_check
from .m6_preflight import run_m6_preflight_check
from .m6_recovery import run_m6_recovery_drill
from .m6_scheduler import run_m6_scheduler_readiness_check
from .m6_final_freeze import run_m6_final_freeze_check
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
    "ALLOWED_MEMORY_AUTHORITIES",
    "ALLOWED_MEMORY_DECISIONS",
    "ALLOWED_MEMORY_RISKS",
    "ALLOWED_MEMORY_TYPES",
    "CompanionPaths",
    "ConservativeGroundingEvaluator",
    "ConservativeMemoryEvaluator",
    "DEEPSEEK_API_KEY_ENV",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_DEFAULT_MODEL",
    "DialogueEngine",
    "DialogueResult",
    "DialogueRunner",
    "DialogueReplayCheckResult",
    "DialogueTurnResult",
    "FakeLLMClient",
    "HttpLLMError",
    "JsonMemoryStore",
    "LLMProviderConfigError",
    "LifeLoopRunner",
    "GroundingEvaluation",
    "GroundedOutputRepairer",
    "M7DialogueFreezeResult",
    "M7MemoryProposalGateResult",
    "MEMORY_DECISION_SCHEMA",
    "M8MemoryPolicyLedgerResult",
    "M8MemoryStewardResult",
    "M8DialogueHumanityResult",
    "M8MemoryFreezeResult",
    "M8MemoryReviewQueueResult",
    "M9SchedulerDryRunResult",
    "M9SchedulerActivationResult",
    "M9SchedulerTickResult",
    "M9PresenceObservationResult",
    "M9SchedulerRevalidationResult",
    "MemoryDecision",
    "MemoryDecisionValidationError",
    "MemoryEvaluation",
    "MemoryReviewError",
    "MemoryRetrievalResult",
    "OllamaClient",
    "OpenAICompatibleClient",
    "SUPPORTED_LLM_PROVIDERS",
    "SemanticFirstMemoryStore",
    "WakeResult",
    "WakeCommandResult",
    "RepairResult",
    "ReplayResult",
    "ReplayRunner",
    "RetrievedMemory",
    "SemanticShadowWriter",
    "append_memory_decision",
    "append_memory_decisions",
    "append_scheduler_attempts",
    "append_wake_event",
    "approve_memory_review_decision",
    "archive_memory_review_decision",
    "assemble_dialogue_memory_context",
    "check_llm_provider",
    "check_runtime_readiness",
    "build_memory_proposals",
    "build_m9_cron_line",
    "discover_m9_scheduler_inventory",
    "disable_command",
    "enable_command",
    "initialize_scheduler_presence_state",
    "load_dialogue_context",
    "parse_dialogue_output",
    "check_dialogue_transcript",
    "run_m7_dialogue_freeze_check",
    "run_m7_memory_proposal_gate",
    "run_m8_memory_policy_ledger",
    "run_m8_dialogue_humanity_regression",
    "run_m8_memory_retrieval_check",
    "run_m8_memory_freeze_check",
    "run_m8_memory_review_queue_check",
    "run_m8_memory_steward_readonly",
    "run_m9_scheduler_dry_run",
    "run_m9_scheduler_activation",
    "run_m9_scheduler_disable",
    "run_m9_scheduler_tick",
    "run_m9_presence_observation",
    "run_m9_scheduler_revalidation_check",
    "write_m7_dialogue_freeze_report",
    "write_m7_memory_proposal_report",
    "write_m8_memory_policy_ledger_report",
    "write_m8_dialogue_humanity_report",
    "write_m8_memory_retrieval_report",
    "write_m8_memory_freeze_report",
    "write_m8_memory_review_queue_report",
    "write_m8_memory_steward_report",
    "write_m9_scheduler_dry_run_report",
    "write_m9_scheduler_activation_report",
    "write_m9_presence_observation_report",
    "write_m9_scheduler_revalidation_report",
    "create_llm_client",
    "build_trial_summary",
    "load_memory_decisions",
    "load_memory_review_actions",
    "load_memory_review_queue",
    "load_scheduler_attempts",
    "load_wake_events",
    "load_local_secrets",
    "normalize_memory_decision",
    "reject_memory_review_decision",
    "source_only_m9_scheduler_inventory",
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
    "run_m6_pi_observation_check",
    "run_m6_preflight_check",
    "run_m6_recovery_drill",
    "run_m6_scheduler_readiness_check",
    "run_m6_final_freeze_check",
    "run_m4_observation_check",
    "run_m4_wake_trial",
    "validate_memory_decision",
    "classify_wake_trial_failure",
    "audit_semantic_shadow_authority",
]
