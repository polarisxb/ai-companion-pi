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
from .m9_presence_freeze import (
    M9PresenceFreezeResult,
    run_m9_presence_freeze,
    write_m9_presence_freeze_report,
)
from .signal_transport import (
    FakeSignalTransport,
    InboundSignalMessage,
    SignalCliTransport,
    SignalCliUnavailableError,
    SignalTransportError,
    parse_signal_envelope_line,
)
from .signal_chat import (
    SIGNAL_CHAT_BOUNDARIES,
    SIGNAL_CHAT_SKIP_REASONS,
    SIGNAL_OUTBOUND_DEFER_REASONS,
    SIGNAL_OUTBOUND_SKIP_REASONS,
    FailingDialogueLLMClient,
    SignalChatBridge,
    SignalChatConfig,
    SignalChatConfigError,
    SignalChatLockError,
    StaticDialogueLLMClient,
    append_signal_chat_attempts,
    channel_conversation_id,
    evaluate_signal_message,
    load_feishu_chat_config,
    load_m10_freeze_evidence,
    load_signal_chat_attempts,
    load_signal_chat_config,
    load_signal_chat_state,
    outbound_defer_reason,
    save_signal_chat_state,
    signal_conversation_id,
)
from .m10_signal_dry_run import (
    M10SignalDryRunResult,
    run_m10_signal_dry_run,
    write_m10_signal_dry_run_report,
)
from .m10_signal_trial import (
    M10SignalTrialResult,
    run_m10_signal_trial,
    write_m10_signal_trial_report,
)
from .m10_signal_activation import (
    M10SignalActivationResult,
    build_signal_chat_unit,
    disable_signal_chat_command,
    enable_signal_chat_command,
    run_m10_signal_activation,
    run_m10_signal_disable,
    write_m10_signal_activation_report,
)
from .m10_signal_observation import (
    M10SignalObservationResult,
    run_m10_signal_observation,
    write_m10_signal_observation_report,
)
from .m10_signal_freeze import (
    M10SignalFreezeResult,
    run_m10_signal_freeze,
    write_m10_signal_freeze_report,
)
from .signal_outbox import (
    append_signal_outbox_entry,
    build_signal_outbox_entry,
    load_signal_outbox_entries,
    normalize_signal_section,
)
from .m11_outbound_dry_run import (
    M11OutboundDryRunResult,
    run_m11_outbound_dry_run,
    write_m11_outbound_dry_run_report,
)
from .m11_outbound_trial import (
    M11OutboundTrialResult,
    run_m11_outbound_trial,
    write_m11_outbound_trial_report,
)
from .m11_outbound_observation import (
    M11OutboundObservationResult,
    run_m11_outbound_observation,
    write_m11_outbound_observation_report,
)
from .m11_outbound_freeze import (
    M11OutboundFreezeResult,
    run_m11_outbound_freeze,
    write_m11_outbound_freeze_report,
)
from .semantic_retrieval import (
    HashingEmbeddingBackend,
    SemanticRetrievalConfig,
    SemanticRetrievalConfigError,
    SentenceTransformerEmbeddingBackend,
    apply_semantic_ranking,
    cosine_similarity,
    create_embedding_backend,
    load_semantic_index,
    load_semantic_retrieval_config,
    save_semantic_index,
    summarize_index_coverage,
)
from .m12_semantic_readiness import (
    M12SemanticReadinessResult,
    run_m12_semantic_readiness,
    write_m12_semantic_readiness_report,
)
from .m12_semantic_retrieval_check import (
    M12SemanticRetrievalCheckResult,
    run_m12_semantic_retrieval_check,
    write_m12_semantic_retrieval_report,
)
from .m12_semantic_backfill import (
    M12SemanticBackfillResult,
    run_m12_semantic_backfill,
    write_m12_semantic_backfill_report,
)
from .m12_semantic_observation import (
    M12SemanticObservationResult,
    run_m12_semantic_observation,
    write_m12_semantic_observation_report,
)
from .m12_semantic_freeze import (
    M12SemanticFreezeResult,
    run_m12_semantic_freeze,
    write_m12_semantic_freeze_report,
)
from .feishu_transport import (
    FakeFeishuTransport,
    FeishuApiClient,
    FeishuApiError,
    FeishuCredentialsError,
    FeishuSdkUnavailableError,
    FeishuTransport,
    parse_feishu_message_event,
)
from .m13_feishu_dry_run import (
    M13FeishuDryRunResult,
    run_m13_feishu_dry_run,
    write_m13_feishu_dry_run_report,
)
from .m13_feishu_trial import (
    M13FeishuTrialResult,
    run_m13_feishu_trial,
    write_m13_feishu_trial_report,
)
from .m13_feishu_activation import (
    M13FeishuActivationResult,
    build_feishu_chat_unit,
    disable_feishu_chat_command,
    enable_feishu_chat_command,
    run_m13_feishu_activation,
    run_m13_feishu_disable,
    write_m13_feishu_activation_report,
)
from .m13_feishu_observation import (
    M13FeishuObservationResult,
    run_m13_feishu_observation,
    write_m13_feishu_observation_report,
)
from .m13_feishu_freeze import (
    M13FeishuFreezeResult,
    run_m13_feishu_freeze,
    write_m13_feishu_freeze_report,
)
from .tts import (
    CommandTTSBackend,
    FakeTTSBackend,
    SynthesizedVoice,
    TTSError,
    create_tts_backend,
)
from .chat_media import (
    deliver_reply_media,
    media_prompt_hints,
    validate_image_attachments,
    voice_decision,
)
from .m14_feishu_media_dry_run import (
    M14FeishuMediaDryRunResult,
    run_m14_feishu_media_dry_run,
    write_m14_feishu_media_dry_run_report,
)
from .m14_feishu_media_trial import (
    M14FeishuMediaTrialResult,
    run_m14_feishu_media_trial,
    write_m14_feishu_media_trial_report,
)
from .m14_feishu_media_observation import (
    M14FeishuMediaObservationResult,
    run_m14_feishu_media_observation,
    write_m14_feishu_media_observation_report,
)
from .m14_feishu_media_freeze import (
    M14FeishuMediaFreezeResult,
    run_m14_feishu_media_freeze,
    write_m14_feishu_media_freeze_report,
)
from .m15_consolidation_dry_run import (
    M15ConsolidationDryRunResult,
    run_m15_consolidation_dry_run,
    write_m15_consolidation_dry_run_report,
)
from .consolidation import (
    ConsolidationConfig,
    ConsolidationConfigError,
    ConsolidationPlanEvaluation,
    apply_consolidation_plan,
    build_consolidation_prompt,
    consolidation_due,
    evaluate_consolidation_plan,
    load_consolidation_config,
    load_consolidation_ledger,
    load_consolidation_plan,
    load_consolidation_state,
    parse_consolidation_output,
    persist_consolidation_plan,
    rollback_consolidation_plan,
    run_consolidation_once,
    save_consolidation_state,
    select_memories_for_review,
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
    "M9PresenceFreezeResult",
    "M9SchedulerRevalidationResult",
    "M10SignalDryRunResult",
    "M10SignalTrialResult",
    "M10SignalActivationResult",
    "M10SignalObservationResult",
    "M10SignalFreezeResult",
    "M11OutboundDryRunResult",
    "M11OutboundTrialResult",
    "M11OutboundObservationResult",
    "M11OutboundFreezeResult",
    "M12SemanticReadinessResult",
    "M12SemanticRetrievalCheckResult",
    "M12SemanticBackfillResult",
    "M12SemanticObservationResult",
    "M12SemanticFreezeResult",
    "HashingEmbeddingBackend",
    "SemanticRetrievalConfig",
    "SemanticRetrievalConfigError",
    "SentenceTransformerEmbeddingBackend",
    "SIGNAL_OUTBOUND_DEFER_REASONS",
    "SIGNAL_OUTBOUND_SKIP_REASONS",
    "FailingDialogueLLMClient",
    "FakeSignalTransport",
    "InboundSignalMessage",
    "SIGNAL_CHAT_BOUNDARIES",
    "SIGNAL_CHAT_SKIP_REASONS",
    "SignalChatBridge",
    "SignalChatConfig",
    "SignalChatConfigError",
    "SignalChatLockError",
    "SignalCliTransport",
    "SignalCliUnavailableError",
    "SignalTransportError",
    "StaticDialogueLLMClient",
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
    "append_signal_chat_attempts",
    "append_wake_event",
    "channel_conversation_id",
    "evaluate_signal_message",
    "load_feishu_chat_config",
    "load_m10_freeze_evidence",
    "load_signal_chat_attempts",
    "load_signal_chat_config",
    "load_signal_chat_state",
    "parse_signal_envelope_line",
    "parse_feishu_message_event",
    "FeishuApiClient",
    "FeishuApiError",
    "FeishuCredentialsError",
    "FeishuSdkUnavailableError",
    "FeishuTransport",
    "FakeFeishuTransport",
    "M13FeishuDryRunResult",
    "M13FeishuTrialResult",
    "M13FeishuActivationResult",
    "M13FeishuObservationResult",
    "M13FeishuFreezeResult",
    "M14FeishuMediaDryRunResult",
    "M14FeishuMediaTrialResult",
    "M14FeishuMediaObservationResult",
    "M14FeishuMediaFreezeResult",
    "CommandTTSBackend",
    "FakeTTSBackend",
    "SynthesizedVoice",
    "TTSError",
    "create_tts_backend",
    "ConsolidationConfig",
    "ConsolidationConfigError",
    "ConsolidationPlanEvaluation",
    "M15ConsolidationDryRunResult",
    "run_m15_consolidation_dry_run",
    "write_m15_consolidation_dry_run_report",
    "apply_consolidation_plan",
    "build_consolidation_prompt",
    "consolidation_due",
    "evaluate_consolidation_plan",
    "load_consolidation_config",
    "load_consolidation_ledger",
    "load_consolidation_plan",
    "load_consolidation_state",
    "parse_consolidation_output",
    "persist_consolidation_plan",
    "rollback_consolidation_plan",
    "run_consolidation_once",
    "save_consolidation_state",
    "select_memories_for_review",
    "deliver_reply_media",
    "media_prompt_hints",
    "validate_image_attachments",
    "voice_decision",
    "run_m14_feishu_media_dry_run",
    "run_m14_feishu_media_trial",
    "run_m14_feishu_media_observation",
    "run_m14_feishu_media_freeze",
    "write_m14_feishu_media_dry_run_report",
    "write_m14_feishu_media_trial_report",
    "write_m14_feishu_media_observation_report",
    "write_m14_feishu_media_freeze_report",
    "run_m13_feishu_dry_run",
    "run_m13_feishu_trial",
    "run_m13_feishu_activation",
    "run_m13_feishu_disable",
    "run_m13_feishu_observation",
    "run_m13_feishu_freeze",
    "build_feishu_chat_unit",
    "enable_feishu_chat_command",
    "disable_feishu_chat_command",
    "write_m13_feishu_dry_run_report",
    "write_m13_feishu_trial_report",
    "write_m13_feishu_activation_report",
    "write_m13_feishu_observation_report",
    "write_m13_feishu_freeze_report",
    "run_m10_signal_dry_run",
    "run_m10_signal_trial",
    "run_m10_signal_activation",
    "run_m10_signal_disable",
    "run_m10_signal_observation",
    "run_m10_signal_freeze",
    "build_signal_chat_unit",
    "enable_signal_chat_command",
    "disable_signal_chat_command",
    "append_signal_outbox_entry",
    "build_signal_outbox_entry",
    "load_signal_outbox_entries",
    "normalize_signal_section",
    "outbound_defer_reason",
    "run_m11_outbound_dry_run",
    "run_m11_outbound_trial",
    "run_m11_outbound_observation",
    "run_m11_outbound_freeze",
    "apply_semantic_ranking",
    "cosine_similarity",
    "create_embedding_backend",
    "load_semantic_index",
    "load_semantic_retrieval_config",
    "save_semantic_index",
    "summarize_index_coverage",
    "run_m12_semantic_readiness",
    "run_m12_semantic_retrieval_check",
    "run_m12_semantic_backfill",
    "run_m12_semantic_observation",
    "run_m12_semantic_freeze",
    "write_m12_semantic_readiness_report",
    "write_m12_semantic_retrieval_report",
    "write_m12_semantic_backfill_report",
    "write_m12_semantic_observation_report",
    "write_m12_semantic_freeze_report",
    "write_m11_outbound_dry_run_report",
    "write_m11_outbound_trial_report",
    "write_m11_outbound_observation_report",
    "write_m11_outbound_freeze_report",
    "save_signal_chat_state",
    "signal_conversation_id",
    "write_m10_signal_dry_run_report",
    "write_m10_signal_trial_report",
    "write_m10_signal_activation_report",
    "write_m10_signal_observation_report",
    "write_m10_signal_freeze_report",
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
    "run_m9_presence_freeze",
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
    "write_m9_presence_freeze_report",
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
