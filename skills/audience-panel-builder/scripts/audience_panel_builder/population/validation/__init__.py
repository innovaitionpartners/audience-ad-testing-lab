"""Strict Tier 4 held-out validation contracts."""

from .contracts import (
    CLAIM_FAMILY_VERSION,
    AUTHORITY_REGISTRY_VERSION,
    COMPARISON_VERSION,
    EVALUATION_VERSION,
    PREREGISTRATION_VERSION,
    SHARED_OUTCOME_EVIDENCE_VERSION,
    TIER4_CLAIM_VERSION,
    VALIDATION_OBSERVATION_VERSION,
    ValidatedDesignApproval,
    ValidatedAuthorityRegistry,
    approve_preregistration_design,
    design_evidence_sha256,
    load_trusted_authority_registry,
    project_synthetic_result_binding,
    project_shared_outcome_evidence,
    read_protected_authority_secret,
    require_design_approval,
    seal_preregistration,
    validate_claim_family,
    validate_comparison,
    validate_held_out_evaluation,
    validate_preregistration,
    validate_shared_outcome_evidence,
    validate_tier4_claim,
    validate_validation_observation,
)
from .metrics import (
    METRIC_FAMILIES,
    DifferenceInterval,
    NormalizedArm,
    classify_observed_pair,
    normalize_observation,
)
from .statistics import (
    InsufficientUncertaintyError,
    Interval,
    bca_block_interval,
    block_pairwise_agreement,
    complete_block_sign_permutation_p,
    holm_adjust,
    kendall_tau_b,
)
from .evaluation import (
    build_claim_family,
    evaluate_held_out_ordering,
    issue_tier4_claim,
)
from .synthetic import (
    SYNTHETIC_SURFACES,
    FrozenOrdering,
    build_synthetic_outcome_comparison,
    derive_pair_directions,
    load_frozen_ordering,
)
from .evidence_bindings import (
    LINEAGE_ORDER,
    bind_json,
    bind_jsonl,
    lineage_bundle_sha256,
)
from .evidence_errors import (
    ProducerAuthenticationError,
    ProducerEvidenceError,
    ProducerOutputCollision,
    ProducerPublicationIndeterminate,
    ProducerRuntimeUnavailable,
)
from .evidence_snapshot import (
    EvidenceSnapshot,
    ValidatedEvidenceSnapshot,
    create_evidence_snapshot,
    open_evidence_snapshot,
    recover_evidence_snapshot_publication,
)
from .producer_semantics import (
    ProducerSemanticsBundle,
    build_producer_semantics,
)
from .replay_inputs import (
    ProducerReplayInputs,
    assemble_replay_inputs,
)
from .producer_replay import replay_producer
from .producer_evidence import (
    PRODUCER_EVIDENCE_VERSION,
    recover_synthetic_producer_evidence_publication,
    recover_synthetic_producer_revocation_publication,
    validate_synthetic_producer_evidence,
    verify_synthetic_producer,
)
from .package import (
    VALIDATION_GENERATOR_VERSION,
    VALIDATION_PACKAGE_VERSION,
    ValidationPackageError,
    ValidationPackageSafetyError,
    build_validation_package,
    validate_validation_package,
)
from .library import (
    CLAIM_LIFECYCLE_EVENT_VERSION,
    VALIDATION_LIBRARY_VERSION,
    ImmutableVersionConflict,
    LibraryError,
    LibraryLockError,
    LibraryNotFoundError,
    LibrarySafetyError,
    append_claim_lifecycle_event,
    claim_lifecycle_status,
    current_claim,
    list_claims,
    register_validation_package,
    show_claim,
)

__all__ = [
    "CLAIM_FAMILY_VERSION", "COMPARISON_VERSION", "EVALUATION_VERSION",
    "PREREGISTRATION_VERSION", "SHARED_OUTCOME_EVIDENCE_VERSION",
    "TIER4_CLAIM_VERSION", "VALIDATION_OBSERVATION_VERSION",
    "project_synthetic_result_binding", "project_shared_outcome_evidence",
    "read_protected_authority_secret",
    "seal_preregistration",
    "validate_claim_family", "validate_comparison",
    "validate_held_out_evaluation", "validate_preregistration",
    "validate_shared_outcome_evidence", "validate_tier4_claim",
    "validate_validation_observation",
    "METRIC_FAMILIES", "DifferenceInterval", "NormalizedArm",
    "classify_observed_pair", "normalize_observation",
    "InsufficientUncertaintyError", "Interval", "bca_block_interval",
    "block_pairwise_agreement", "complete_block_sign_permutation_p",
    "holm_adjust", "kendall_tau_b",
    "build_claim_family", "evaluate_held_out_ordering", "issue_tier4_claim",
    "SYNTHETIC_SURFACES", "FrozenOrdering", "build_synthetic_outcome_comparison",
    "derive_pair_directions", "load_frozen_ordering",
    "LINEAGE_ORDER", "bind_json", "bind_jsonl", "lineage_bundle_sha256",
    "ProducerAuthenticationError", "ProducerEvidenceError", "ProducerOutputCollision",
    "ProducerPublicationIndeterminate",
    "ProducerRuntimeUnavailable",
    "EvidenceSnapshot", "ValidatedEvidenceSnapshot", "create_evidence_snapshot",
    "open_evidence_snapshot", "recover_evidence_snapshot_publication",
    "ProducerSemanticsBundle", "build_producer_semantics",
    "ProducerReplayInputs", "assemble_replay_inputs",
    "replay_producer",
    "PRODUCER_EVIDENCE_VERSION",
    "verify_synthetic_producer",
    "validate_synthetic_producer_evidence",
    "recover_synthetic_producer_evidence_publication",
    "recover_synthetic_producer_revocation_publication",
    "VALIDATION_GENERATOR_VERSION", "VALIDATION_PACKAGE_VERSION",
    "ValidationPackageError", "ValidationPackageSafetyError",
    "build_validation_package", "validate_validation_package",
    "CLAIM_LIFECYCLE_EVENT_VERSION", "VALIDATION_LIBRARY_VERSION",
    "ImmutableVersionConflict", "LibraryError", "LibraryLockError",
    "LibraryNotFoundError", "LibrarySafetyError",
    "append_claim_lifecycle_event", "claim_lifecycle_status",
    "current_claim", "list_claims",
    "register_validation_package", "show_claim",
]
