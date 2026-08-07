from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "audience-panel-builder" / "scripts"))

import audience_panel_builder  # noqa: E402
from audience_panel_builder.common import ContractError  # noqa: E402
from audience_panel_builder.workflow_state import (  # noqa: E402
    APPROVAL_SCOPES,
    WORKFLOW_STATES,
    WORKFLOW_STATE_SCHEMA_VERSION,
    canonical_workflow_state_bytes,
    require_approved_scope,
    transition_workflow_state,
    validate_workflow_state,
    workflow_state_sha256,
)


HASH_A = "a" * 64
HASH_B = "b" * 64


class PanelWorkflowStateTests(unittest.TestCase):
    def test_package_exports_preserve_existing_surface_and_add_workflow_state(self):
        self.assertEqual(
            [
                "build_source_plan",
                "build_evidence_ledger",
                "normalize_last30days",
                "normalize_mapped_export",
                "score_source_candidates",
                "validate_capability_inventory",
                "validate_evidence_ledger",
                "validate_finding_support",
                "validate_research_intake",
                "validate_source_registry",
                "validate_synthesis_matrix",
                "verified_capabilities",
                "APPROVAL_SCOPES",
                "WORKFLOW_STATES",
                "WORKFLOW_STATE_SCHEMA_VERSION",
                "canonical_workflow_state_bytes",
                "require_approved_scope",
                "transition_workflow_state",
                "validate_workflow_state",
                "workflow_state_sha256",
            ],
            audience_panel_builder.__all__,
        )

    def payload(self, *, state="draft", approvals=None):
        return {
            "schema_version": WORKFLOW_STATE_SCHEMA_VERSION,
            "workflow_id": "operations-leaders-build",
            "panel_id": "operations-leaders",
            "panel_version": "v1",
            "state": state,
            "updated_at": "2026-07-23T12:00:00Z",
            "approvals": [] if approvals is None else approvals,
            "bindings": {
                "brief_sha256": HASH_A,
                "panel_sha256": HASH_B,
                "report_inputs_sha256": None,
                "audit_sha256": None,
                "package_sha256": None,
            },
        }

    def approval(self, scope, status="pending", target=HASH_A):
        approved = status in {"approved", "rejected"}
        return {
            "scope": scope,
            "status": status,
            "approved_by": "sally" if approved else "",
            "approved_at": "2026-07-23T12:30:00Z" if approved else "",
            "target_sha256": target,
            "note": "Reviewed." if approved else "",
        }

    def test_valid_state_is_canonical_and_hashable(self):
        payload = self.payload(approvals=[self.approval("evidence_synthesis")])
        self.assertEqual(payload, validate_workflow_state(payload))
        self.assertEqual(
            canonical_workflow_state_bytes(payload),
            canonical_workflow_state_bytes({key: payload[key] for key in reversed(payload)}),
        )
        self.assertEqual(
            hashlib.sha256(canonical_workflow_state_bytes(payload)).hexdigest(),
            workflow_state_sha256(payload),
        )

    def test_all_declared_states_and_transitions(self):
        transitions = {
            "draft": {"dogfood", "provisional", "approved", "retired"},
            "dogfood": {"draft", "provisional", "approved", "retired"},
            "provisional": {"draft", "approved", "needs_refresh", "retired"},
            "approved": {"needs_refresh", "retired"},
            "needs_refresh": {"draft", "approved", "retired"},
            "retired": set(),
        }
        self.assertEqual(set(transitions), WORKFLOW_STATES)
        for current, allowed in transitions.items():
            for next_state in allowed:
                approvals = []
                if current == "approved" or next_state == "approved":
                    approvals = [
                        self.approval("evidence_synthesis", "approved"),
                        self.approval("panel_construction", "approved", HASH_B),
                    ]
                moved = transition_workflow_state(
                    self.payload(state=current, approvals=approvals),
                    next_state=next_state,
                    updated_at="2026-07-24T12:00:00Z",
                )
                self.assertEqual(next_state, moved["state"])
                self.assertEqual("2026-07-24T12:00:00Z", moved["updated_at"])
            disallowed = WORKFLOW_STATES - allowed - {current}
            for next_state in disallowed:
                approvals = []
                if current == "approved":
                    approvals = [
                        self.approval("evidence_synthesis", "approved"),
                        self.approval("panel_construction", "approved", HASH_B),
                    ]
                with self.assertRaisesRegex(ContractError, "may not transition|terminal"):
                    transition_workflow_state(
                        self.payload(state=current, approvals=approvals),
                        next_state=next_state,
                        updated_at="2026-07-24T12:00:00Z",
                    )

    def test_all_approval_statuses_and_metadata_rules(self):
        for status in {"pending", "approved", "rejected"}:
            self.assertEqual(
                status,
                validate_workflow_state(
                    self.payload(approvals=[self.approval("dogfood", status)])
                )["approvals"][0]["status"],
            )
        for status in {"approved", "rejected"}:
            payload = self.payload(approvals=[self.approval("dogfood", status)])
            payload["approvals"][0]["approved_by"] = ""
            with self.assertRaisesRegex(ContractError, "approved_by"):
                validate_workflow_state(payload)
        payload = self.payload(approvals=[self.approval("dogfood")])
        payload["approvals"][0]["approved_at"] = "2026-07-23T12:30:00Z"
        with self.assertRaisesRegex(ContractError, "pending"):
            validate_workflow_state(payload)

    def test_rejects_unknown_duplicate_and_malformed_contract_values(self):
        payload = self.payload()
        payload["extra"] = True
        with self.assertRaisesRegex(ContractError, "unknown"):
            validate_workflow_state(payload)
        payload = self.payload(approvals=[self.approval("dogfood"), self.approval("dogfood")])
        with self.assertRaisesRegex(ContractError, "duplicated"):
            validate_workflow_state(payload)
        payload = self.payload()
        payload["bindings"]["panel_sha256"] = "sha256:" + HASH_B
        with self.assertRaisesRegex(ContractError, "SHA-256"):
            validate_workflow_state(payload)
        payload = self.payload(approvals=[self.approval("dogfood")])
        payload["approvals"][0]["unexpected"] = "value"
        with self.assertRaisesRegex(ContractError, "unknown"):
            validate_workflow_state(payload)
        payload = self.payload()
        payload["updated_at"] = "2026-07-23"
        with self.assertRaisesRegex(ContractError, "timezone"):
            validate_workflow_state(payload)

    def test_scope_approval_requires_current_exact_target_hash(self):
        payload = self.payload(approvals=[self.approval("package_registration", "approved")])
        self.assertEqual(
            "package_registration",
            require_approved_scope(payload, scope="package_registration", target_sha256=HASH_A)["scope"],
        )
        with self.assertRaisesRegex(ContractError, "exact target"):
            require_approved_scope(payload, scope="package_registration", target_sha256=HASH_B)
        payload["approvals"][0]["target_sha256"] = HASH_B
        with self.assertRaisesRegex(ContractError, "exact target"):
            require_approved_scope(payload, scope="package_registration", target_sha256=HASH_A)

    def test_retired_is_terminal_and_approved_requires_two_scopes(self):
        with self.assertRaisesRegex(ContractError, "terminal"):
            transition_workflow_state(
                self.payload(state="retired"),
                next_state="draft",
                updated_at="2026-07-24T12:00:00Z",
            )
        with self.assertRaisesRegex(ContractError, "evidence_synthesis"):
            transition_workflow_state(
                self.payload(state="draft"),
                next_state="approved",
                updated_at="2026-07-24T12:00:00Z",
            )
        with self.assertRaisesRegex(ContractError, "panel_construction"):
            transition_workflow_state(
                self.payload(approvals=[self.approval("evidence_synthesis", "approved")]),
                next_state="approved",
                updated_at="2026-07-24T12:00:00Z",
            )

    def test_approved_document_itself_requires_both_currently_approved_scopes(self):
        approvals = {
            "evidence_synthesis": self.approval(
                "evidence_synthesis",
                "approved",
            ),
            "panel_construction": self.approval(
                "panel_construction",
                "approved",
                HASH_B,
            ),
        }
        self.assertEqual(
            "approved",
            validate_workflow_state(
                self.payload(
                    state="approved",
                    approvals=list(approvals.values()),
                )
            )["state"],
        )
        for required_scope in ("evidence_synthesis", "panel_construction"):
            with self.subTest(scope=required_scope, condition="missing"):
                with self.assertRaisesRegex(ContractError, required_scope):
                    validate_workflow_state(
                        self.payload(
                            state="approved",
                            approvals=[
                                row
                                for scope, row in approvals.items()
                                if scope != required_scope
                            ],
                        )
                    )
            for status in ("pending", "rejected"):
                stale = copy.deepcopy(list(approvals.values()))
                index = 0 if required_scope == "evidence_synthesis" else 1
                stale[index] = self.approval(
                    required_scope,
                    status,
                    HASH_A if required_scope == "evidence_synthesis" else HASH_B,
                )
                with self.subTest(scope=required_scope, condition=status):
                    with self.assertRaisesRegex(ContractError, required_scope):
                        validate_workflow_state(
                            self.payload(
                                state="approved",
                                approvals=stale,
                            )
                        )


if __name__ == "__main__":
    unittest.main()
