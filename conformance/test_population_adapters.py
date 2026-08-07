from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PANEL_SCRIPTS = ROOT / "skills" / "audience-panel-builder" / "scripts"
RESEARCH_SCRIPTS = ROOT / "skills" / "audience-ad-testing-lab" / "scripts"
sys.path.insert(0, str(PANEL_SCRIPTS))
sys.path.insert(0, str(RESEARCH_SCRIPTS))

from audience_lab.audience_research_v3 import (  # noqa: E402
    validate_frame_request,
    validate_observation_batch,
)
from audience_panel_builder.common import ContractError  # noqa: E402
from audience_panel_builder.population.adapters.aggregate_evidence import (  # noqa: E402
    AggregateEvidenceAdapter,
)
from audience_panel_builder.population.adapters.authorized_handoff import (  # noqa: E402
    AuthorizedHandoffAdapter,
)
from audience_panel_builder.population.adapters.base import (  # noqa: E402
    PopulationAdapter,
)
from audience_panel_builder.population.adapters.bls_oews import (  # noqa: E402
    BlsOewsAdapter,
)
from audience_panel_builder.population.adapters.census_cbp import (  # noqa: E402
    CensusCbpAdapter,
)
from audience_panel_builder.population.adapters.census_susb import (  # noqa: E402
    CensusSusbAdapter,
)
from audience_panel_builder.population.registry import (  # noqa: E402
    SOURCE_REGISTRY_VERSION,
    load_population_adapter,
    route_population_sources,
    validate_source_registry,
)


FIXTURES = ROOT / "conformance" / "fixtures" / "population" / "public-proxy"
REGISTRY_PATH = (
    ROOT
    / "skills"
    / "audience-panel-builder"
    / "references"
    / "audience-source-registry-v2.json"
)
CLI = (
    ROOT
    / "skills"
    / "audience-panel-builder"
    / "scripts"
    / "plan-population-sources.py"
)

RAW_HASHES = {
    "bls-oews-may-2025.json": (
        "sha256:878a78a4b4933d43232a44b4ed4f81813173f8a1ecc0510f635ecbdb49ab42ab"
    ),
    "census-susb-2022.json": (
        "sha256:e264d5a4d6994cbffbf0f07a14f8999170b7a642af57445cd12cda9a2230beea"
    ),
    "census-cbp-2023.json": (
        "sha256:f9e78670900124d905176f083e93eb1b9349b355dd640abf0b3f12988d1c8784"
    ),
}
NORMALIZED_HASHES = {
    "bls-oews-may-2025": (
        "sha256:ee119c8300c2c9183cc6eb5ac1b1364495f1632705455195f7aad899fa28e083"
    ),
    "census-susb-2022": (
        "sha256:485dc0f15c08ff6c0c235a28514b0249bbf416788e78db84e188b840c55f90d9"
    ),
    "census-cbp-2023": (
        "sha256:abf3d0825aa739245595b1972bfa6f975e1a50ca5cfd19510f037fee2b4184b7"
    ),
}


class PopulationRegistryTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    def frame_request(
        self,
        *,
        unit: str = "persons",
        dimensions: list[str] | None = None,
        joints: list[list[str]] | None = None,
        geography: list[str] | None = None,
        evidence_bases: list[str] | None = None,
        capabilities: list[str] | None = None,
        proxies: list[dict[str, str]] | None = None,
        target_audience: str = "Marketing decision makers",
    ) -> dict[str, object]:
        dimensions = dimensions or ["geography", "occupation"]
        joints = joints or [["geography", "occupation"]]
        payload = {
            "schema_version": "audience-frame-request-v1",
            "request_id": "marketing-frame-request",
            "target_audience": target_audience,
            "decision": "Construct a defensible frame",
            "desired_claim": "Directional composition claim",
            "geography": geography or ["US"],
            "time_basis": {"as_of": "2026-07-24", "lookback_days": 1825},
            "target_unit": unit,
            "proxy_universes": [
                {**proxy, "exact": proxy.get("exact", False)}
                for proxy in (proxies or [])
            ],
            "required_dimensions": dimensions,
            "required_joints": joints,
            "modeled_cell_rules": [],
            "calibration_rules": [],
            "exclusions": [],
            "authorized_evidence_bases": evidence_bases or ["public"],
            "available_capabilities": capabilities or ["public-adapter"],
            "downgrade_policy": {
                "allow_tier_1": True,
                "allow_experimental": False,
                "reason": "Fail instead of inventing coverage.",
            },
        }
        return validate_frame_request(payload)

    def capabilities(
        self,
        available: list[str] | None = None,
        authentication: list[str] | None = None,
    ) -> dict[str, object]:
        return {
            "available_capabilities": (
                ["public-adapter"] if available is None else available
            ),
            "available_authentication": (
                ["none"] if authentication is None else authentication
            ),
        }

    def test_registry_is_strict_and_descriptors_match_all_five_adapters(self):
        self.assertEqual("audience-source-registry-v2", SOURCE_REGISTRY_VERSION)
        validated = validate_source_registry(self.registry)
        self.assertEqual(
            [
                "bls-oews-may-2025",
                "census-susb-2022",
                "census-cbp-2023",
                "authorized-audience-data-lab-handoff",
                "approved-aggregate-evidence-handoff",
            ],
            [item["adapter_id"] for item in validated["sources"]],
        )
        classes = [
            BlsOewsAdapter,
            CensusSusbAdapter,
            CensusCbpAdapter,
            AuthorizedHandoffAdapter,
            AggregateEvidenceAdapter,
        ]
        for expected, adapter_class in zip(validated["sources"], classes):
            adapter = adapter_class()
            self.assertIsInstance(adapter, PopulationAdapter)
            self.assertEqual(expected, adapter.descriptor())
            self.assertIsInstance(load_population_adapter(expected), adapter_class)

        invalid = deepcopy(self.registry)
        invalid["sources"][0]["audience_names"] = ["marketing leaders"]
        with self.assertRaisesRegex(ContractError, "unknown fields.*audience_names"):
            validate_source_registry(invalid)

    def test_registry_rejects_evidence_basis_outside_task_3_vocabulary(self):
        invalid = deepcopy(self.registry)
        invalid["sources"][0]["access"]["evidence_basis"] = "invented_basis"
        with self.assertRaisesRegex(
            ContractError,
            r"\$\.sources\[0\]\.access\.evidence_basis must be one of:",
        ):
            validate_source_registry(invalid)

    def test_loader_requires_protocol_and_exact_descriptor_match(self):
        non_adapter = deepcopy(self.registry["sources"][0])
        non_adapter["implementation"] = "pathlib:Path"
        with self.assertRaisesRegex(
            ContractError,
            "does not implement PopulationAdapter",
        ):
            load_population_adapter(non_adapter)

        mismatched = deepcopy(self.registry["sources"][0])
        mismatched["adapter_id"] = "bls-oews-mismatched"
        with self.assertRaisesRegex(
            ContractError,
            "descriptor does not exactly match registry",
        ):
            load_population_adapter(mismatched)

    def test_routing_is_property_based_deterministic_and_audience_name_blind(self):
        request = self.frame_request()
        first = route_population_sources(
            frame_request=request,
            registry=self.registry,
            capabilities=self.capabilities(),
        )
        second = route_population_sources(
            frame_request={
                **request,
                "target_audience": "A completely different audience name",
            },
            registry=self.registry,
            capabilities=self.capabilities(),
        )
        self.assertEqual(first, second)
        self.assertEqual("population-source-plan-v1", first["schema_version"])
        self.assertEqual(
            ["bls-oews-may-2025"],
            [item["adapter_id"] for item in first["selections"]],
        )
        selection = first["selections"][0]
        self.assertEqual(["persons"], selection["units"])
        self.assertEqual(["geography", "occupation"], selection["matched_dimensions"])
        self.assertEqual(
            [["geography", "occupation"]], selection["matched_joints"]
        )
        self.assertEqual(["US"], selection["matched_geographies"])
        self.assertEqual("public", selection["access"]["evidence_basis"])
        self.assertEqual("none", selection["authentication"]["mode"])
        self.assertEqual("May 2025", selection["freshness"]["edition"])
        self.assertEqual("2026-05-15", selection["freshness"]["published_at"])

    def test_multi_unit_route_preserves_person_firm_and_establishment_denominators(self):
        request = self.frame_request(
            dimensions=[
                "geography",
                "occupation",
                "industry",
                "enterprise-size",
                "establishment-size",
            ],
            joints=[
                ["geography", "occupation"],
                ["industry", "enterprise-size"],
                ["industry", "establishment-size"],
            ],
            proxies=[
                {
                    "universe_id": "employer-firms",
                    "description": "Employer firms",
                    "unit": "firms",
                    "denominator": "employer-firms",
                },
                {
                    "universe_id": "employer-establishments",
                    "description": "Employer establishments",
                    "unit": "establishments",
                    "denominator": "employer-establishments",
                },
            ],
        )
        plan = route_population_sources(
            frame_request=request,
            registry=self.registry,
            capabilities=self.capabilities(),
        )
        self.assertEqual(
            {
                "bls-oews-may-2025": ["persons"],
                "census-susb-2022": ["firms"],
                "census-cbp-2023": ["establishments"],
            },
            {
                item["adapter_id"]: item["units"]
                for item in plan["selections"]
            },
        )

    def test_same_unit_route_combines_sources_to_cover_required_joints(self):
        geography_occupation = deepcopy(self.registry["sources"][0])
        geography_occupation.update(
            {
                "adapter_id": "persons-geography-occupation",
                "dimensions": ["geography", "occupation"],
                "joints": [["geography", "occupation"]],
            }
        )
        industry_occupation = deepcopy(self.registry["sources"][0])
        industry_occupation.update(
            {
                "adapter_id": "persons-industry-occupation",
                "dimensions": ["industry", "occupation"],
                "joints": [["industry", "occupation"]],
            }
        )
        registry = {
            "schema_version": SOURCE_REGISTRY_VERSION,
            "updated_at": "2026-07-24",
            "sources": [industry_occupation, geography_occupation],
        }
        request = self.frame_request(
            dimensions=["geography", "industry", "occupation"],
            joints=[
                ["geography", "occupation"],
                ["industry", "occupation"],
            ],
        )

        first = route_population_sources(
            frame_request=request,
            registry=registry,
            capabilities=self.capabilities(),
        )
        second = route_population_sources(
            frame_request=request,
            registry={
                **registry,
                "sources": list(reversed(registry["sources"])),
            },
            capabilities=self.capabilities(),
        )

        self.assertEqual(first, second)
        self.assertEqual(
            [
                "persons-geography-occupation",
                "persons-industry-occupation",
            ],
            [item["adapter_id"] for item in first["selections"]],
        )
        self.assertEqual(
            {
                ("geography", "occupation"),
                ("industry", "occupation"),
            },
            {
                tuple(joint)
                for item in first["selections"]
                for joint in item["matched_joints"]
            },
        )

    def test_routing_fails_explicitly_without_capability_or_authentication(self):
        with self.assertRaisesRegex(ContractError, "missing capability: public-adapter"):
            route_population_sources(
                frame_request=self.frame_request(),
                registry=self.registry,
                capabilities=self.capabilities(available=[]),
            )

        request = self.frame_request(
            unit="eligible-cohort-member",
            dimensions=["company-size", "role"],
            joints=[["company-size", "role"]],
            evidence_bases=["first_party_aggregate"],
            capabilities=["authorized-handoff"],
        )
        with self.assertRaisesRegex(
            ContractError,
            "unavailable authentication: audience-data-lab-handoff",
        ):
            route_population_sources(
                frame_request=request,
                registry=self.registry,
                capabilities=self.capabilities(
                    available=["authorized-handoff"],
                    authentication=[],
                ),
            )

    def test_routing_fails_explicitly_on_geography_unit_joint_and_access(self):
        cases = [
            (
                self.frame_request(geography=["GB"]),
                self.capabilities(),
                "unsupported geography: GB",
            ),
            (
                self.frame_request(unit="households"),
                self.capabilities(),
                "incompatible unit: households",
            ),
            (
                self.frame_request(
                    dimensions=["geography", "occupation", "industry"],
                    joints=[["geography", "industry"]],
                ),
                self.capabilities(),
                "missing critical joint: geography \\+ industry",
            ),
            (
                self.frame_request(evidence_bases=["first_party_aggregate"]),
                self.capabilities(),
                "unavailable access basis: first_party_aggregate",
            ),
        ]
        for request, capabilities, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ContractError, message):
                    route_population_sources(
                        frame_request=request,
                        registry=self.registry,
                        capabilities=capabilities,
                    )

    def test_routing_rejects_future_sources_and_invalid_frame_contracts(self):
        future = deepcopy(self.registry)
        future["sources"][0]["freshness"]["published_at"] = "2026-07-25"
        with self.assertRaisesRegex(
            ContractError,
            "no source was published by 2026-07-24",
        ):
            route_population_sources(
                frame_request=self.frame_request(),
                registry=future,
                capabilities=self.capabilities(),
            )

        invalid = self.frame_request()
        invalid["audience_name_alias"] = "must not become a routing input"
        with self.assertRaisesRegex(ContractError, "unknown fields.*audience_name_alias"):
            route_population_sources(
                frame_request=invalid,
                registry=self.registry,
                capabilities=self.capabilities(),
            )

    def test_cli_writes_the_same_validated_plan_without_network_access(self):
        request = self.frame_request()
        capabilities = self.capabilities()
        expected = route_population_sources(
            frame_request=request,
            registry=self.registry,
            capabilities=capabilities,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frame_path = root / "frame.json"
            capabilities_path = root / "capabilities.json"
            output = root / "plan.json"
            frame_path.write_text(json.dumps(request), encoding="utf-8")
            capabilities_path.write_text(json.dumps(capabilities), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "--frame-request",
                    str(frame_path),
                    "--registry",
                    str(REGISTRY_PATH),
                    "--capabilities",
                    str(capabilities_path),
                    "--output",
                    str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(expected, json.loads(output.read_text(encoding="utf-8")))


class PublicPopulationAdapterTests(unittest.TestCase):
    maxDiff = None

    def fixture(self, name: str) -> dict[str, object]:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def mapping(self, adapter_id: str) -> dict[str, str]:
        return {
            "batch_id": f"{adapter_id}-operations",
            "frame_request_id": "operations-frame-request",
        }

    def test_bls_normalizes_pinned_modeled_person_estimates_with_rse(self):
        batch = BlsOewsAdapter().normalize(
            self.fixture("bls-oews-may-2025.json"),
            self.mapping("bls-oews-may-2025"),
        )
        self.assertEqual(batch, validate_observation_batch(batch))
        self.assertEqual(RAW_HASHES["bls-oews-may-2025.json"], batch["raw_snapshot_sha256"])
        self.assertEqual(
            NORMALIZED_HASHES["bls-oews-may-2025"],
            batch["normalized_batch_sha256"],
        )
        self.assertEqual("persons", batch["unit"])
        self.assertEqual("employed-persons-excluding-self-employed", batch["denominator"])
        self.assertEqual("May 2025", batch["source"]["edition"])
        self.assertEqual(
            [21470.0, 395240.0],
            [cell["estimate"] for cell in batch["cells"]],
        )
        self.assertEqual(
            ["modeled", "modeled"],
            [cell["status"] for cell in batch["cells"]],
        )
        self.assertEqual(
            {"lower": 20712.538, "upper": 22227.462, "method": "relative-standard-error-95-percent"},
            batch["cells"][0]["uncertainty"],
        )
        self.assertTrue(
            batch["cells"][0]["source_location"].endswith("#occupation=11-2011")
        )

    def test_susb_normalizes_firms_without_substituting_establishments(self):
        batch = CensusSusbAdapter().normalize(
            self.fixture("census-susb-2022.json"),
            self.mapping("census-susb-2022"),
        )
        self.assertEqual(batch, validate_observation_batch(batch))
        self.assertEqual(RAW_HASHES["census-susb-2022.json"], batch["raw_snapshot_sha256"])
        self.assertEqual(
            NORMALIZED_HASHES["census-susb-2022"],
            batch["normalized_batch_sha256"],
        )
        self.assertEqual("firms", batch["unit"])
        self.assertEqual("employer-firms", batch["denominator"])
        self.assertEqual("2022-12-31", batch["source"]["vintage"])
        self.assertEqual(
            [872305.0, 868529.0, 547.0],
            [cell["estimate"] for cell in batch["cells"]],
        )
        self.assertTrue(all(cell["status"] == "observed" for cell in batch["cells"]))
        self.assertTrue(
            all(
                cell["uncertainty"]["method"]
                == "published-firm-count-no-interval"
                for cell in batch["cells"]
            )
        )
        self.assertEqual(
            {"lower": 872305.0, "upper": 872305.0, "method": "published-firm-count-no-interval"},
            batch["cells"][0]["uncertainty"],
        )

    def test_cbp_preserves_establishments_suppression_and_missing_status(self):
        batch = CensusCbpAdapter().normalize(
            self.fixture("census-cbp-2023.json"),
            self.mapping("census-cbp-2023"),
        )
        self.assertEqual(batch, validate_observation_batch(batch))
        self.assertEqual(RAW_HASHES["census-cbp-2023.json"], batch["raw_snapshot_sha256"])
        self.assertEqual(
            NORMALIZED_HASHES["census-cbp-2023"],
            batch["normalized_batch_sha256"],
        )
        self.assertEqual("establishments", batch["unit"])
        self.assertEqual("employer-establishments", batch["denominator"])
        self.assertEqual("2023-12-31", batch["source"]["vintage"])
        self.assertEqual(
            {"lower": 989182.0, "upper": 989182.0, "method": "published-establishment-count-no-interval"},
            batch["cells"][0]["uncertainty"],
        )
        suppressed = batch["cells"][2]
        self.assertIsNone(suppressed["estimate"])
        self.assertEqual(
            {"lower": None, "upper": None, "method": "not-available-suppression-n"},
            suppressed["uncertainty"],
        )
        self.assertTrue(suppressed["suppressed"])
        self.assertEqual("missing", suppressed["status"])
        self.assertEqual(
            "not-available-suppression-n",
            suppressed["uncertainty"]["method"],
        )

    def test_public_acquisition_is_pinned_local_hash_checked_and_no_clobber(self):
        source = FIXTURES / "bls-oews-may-2025.json"
        adapter = BlsOewsAdapter()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "snapshot.json"
            payload = adapter.acquire(
                {
                    "snapshot_path": str(source),
                    "snapshot_sha256": RAW_HASHES[source.name],
                    "network_acquisition": False,
                },
                destination,
            )
            self.assertEqual(self.fixture(source.name), payload)
            self.assertTrue(destination.is_file())
            with self.assertRaisesRegex(ContractError, "already exists"):
                adapter.acquire(
                    {
                        "snapshot_path": str(source),
                        "snapshot_sha256": RAW_HASHES[source.name],
                        "network_acquisition": False,
                    },
                    destination,
                )
        with self.assertRaisesRegex(ContractError, "explicit integration route"):
            adapter.acquire(
                {
                    "snapshot_path": "https://www.bls.gov/oes/",
                    "snapshot_sha256": RAW_HASHES[source.name],
                    "network_acquisition": True,
                },
                Path("/tmp/not-created-by-population-adapter.json"),
            )

    def test_public_finish_boundary_rejects_invalid_task_3_batch(self):
        with self.assertRaisesRegex(ContractError, r"\$\.batch_id"):
            BlsOewsAdapter().normalize(
                self.fixture("bls-oews-may-2025.json"),
                {
                    "batch_id": "NOT A CANONICAL ID",
                    "frame_request_id": "operations-frame-request",
                },
            )


class HandoffPopulationAdapterTests(unittest.TestCase):
    maxDiff = None

    def approved_aggregate_handoff(self) -> dict[str, object]:
        return {
            "schema_version": "audience-first-party-evidence-v1",
            "package_id": "operations-first-party-evidence",
            "created_at": "2026-07-24T12:00:00Z",
            "status": "approved",
            "source_audit_sha256": "sha256:" + "1" * 64,
            "input_sha256": "sha256:" + "2" * 64,
            "purpose": "Build an aggregate audience frame",
            "covered_population": "Approved operations cohort",
            "time_window": {
                "start": "2026-01-01",
                "end": "2026-06-30",
                "timezone": "UTC",
            },
            "evidence_basis": "permissioned_first_party_aggregate",
            "data_quality": {"entity_count": 100},
            "distributions": [
                {
                    "dimensions": {"role": "operations"},
                    "count": 60,
                    "share": 0.6,
                    "suppressed": False,
                },
                {
                    "dimensions": {"role": "finance"},
                    "count": 40,
                    "share": 0.4,
                    "suppressed": False,
                },
                {
                    "dimensions": {"role": "[suppressed]"},
                    "count": None,
                    "share": None,
                    "suppressed": True,
                },
            ],
            "cross_tabs": [],
            "segment_candidates": {"status": "not_run"},
            "privacy_assessment": {"minimum_cell_size": 10},
            "allowed_uses": ["audience_panel_research"],
            "prohibited_uses": ["identity_reconstruction"],
            "limitations": ["The supplied cohort may not represent the market."],
            "approval": {
                "approved_for_downstream_use": True,
                "approved_by": "Data owner",
                "approved_at": "2026-07-24T12:30:00Z",
                "approval_note": "Approved aggregate use.",
            },
        }

    def test_approved_aggregate_adapter_preserves_derived_and_missing_statuses(self):
        batch = AggregateEvidenceAdapter().normalize(
            self.approved_aggregate_handoff(),
            {
                "batch_id": "approved-aggregate-operations",
                "frame_request_id": "operations-frame-request",
                "geography": ["US"],
                "unit": "eligible-cohort-member",
                "denominator": "approved-cohort-members",
                "dimensions": ["role"],
                "estimate_field": "share",
            },
        )
        self.assertEqual(batch, validate_observation_batch(batch))
        self.assertEqual(
            ["derived", "derived", "missing"],
            [cell["status"] for cell in batch["cells"]],
        )
        self.assertEqual([0.6, 0.4, None], [cell["estimate"] for cell in batch["cells"]])
        self.assertEqual(
            {"lower": 0.6, "upper": 0.6, "method": "exact-approved-aggregate-for-covered-cohort"},
            batch["cells"][0]["uncertainty"],
        )
        self.assertEqual(
            {"lower": None, "upper": None, "method": "suppressed-approved-aggregate"},
            batch["cells"][2]["uncertainty"],
        )
        self.assertTrue(batch["cells"][2]["suppressed"])
        self.assertRegex(batch["raw_snapshot_sha256"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(batch["normalized_batch_sha256"], r"^sha256:[0-9a-f]{64}$")

    def test_aggregate_descriptor_excludes_performance_calibration_program(self):
        self.assertEqual(
            ["audience-first-party-evidence-v1"],
            AggregateEvidenceAdapter().descriptor()["programs"],
        )

    def test_aggregate_finish_boundary_rejects_invalid_task_3_batch(self):
        with self.assertRaisesRegex(ContractError, r"\$\.batch_id"):
            AggregateEvidenceAdapter().normalize(
                self.approved_aggregate_handoff(),
                {
                    "batch_id": "NOT A CANONICAL ID",
                    "frame_request_id": "operations-frame-request",
                    "geography": ["US"],
                    "unit": "eligible-cohort-member",
                    "denominator": "approved-cohort-members",
                    "dimensions": ["role"],
                    "estimate_field": "share",
                },
            )

    def test_aggregate_adapter_rejects_draft_or_wrong_use_handoffs(self):
        draft = self.approved_aggregate_handoff()
        draft["status"] = "draft"
        draft["approval"] = {
            "approved_for_downstream_use": False,
            "approved_by": None,
            "approved_at": None,
            "approval_note": None,
        }
        with self.assertRaisesRegex(ContractError, "must be approved"):
            AggregateEvidenceAdapter().normalize(draft, {})

        wrong_use = self.approved_aggregate_handoff()
        wrong_use["allowed_uses"] = ["reporting"]
        with self.assertRaisesRegex(ContractError, "audience_panel_research"):
            AggregateEvidenceAdapter().normalize(wrong_use, {})

    def test_authorized_adapter_accepts_only_canonical_handoff_boundary(self):
        adapter = AuthorizedHandoffAdapter()
        with self.assertRaisesRegex(ContractError, "only canonical handoff"):
            adapter.normalize(
                {
                    "handoff": {},
                    "output_root": "/tmp/approved-output",
                    "client_source_files": ["/private/client.csv"],
                },
                None,
            )
        with self.assertRaisesRegex(ContractError, "authorized handoff"):
            adapter.normalize(
                {"handoff": {}, "output_root": "/tmp/approved-output"},
                None,
            )
        with self.assertRaisesRegex(ContractError, "does not acquire source files"):
            adapter.acquire({}, Path("/tmp/not-created-by-authorized-adapter.json"))

    def test_authorized_adapter_validates_real_handoff_hashes_without_source_reopen(self):
        from audience_data_lab.authorized_transform import transform_authorized_bundle
        from conformance.test_authorized_audience_transform import (
            AuthorizedAudienceTransformTests,
        )

        case = AuthorizedAudienceTransformTests(
            "test_transform_writes_valid_hash_bound_outputs_without_clobbering"
        )
        case.setUp()
        try:
            output = case.root / "handoff"
            handoff = transform_authorized_bundle(
                source_profile=case.profile,
                mapping=case._mapping(),
                input_root=case.source,
                output_dir=output,
                transformer_version="1.0.0",
            )
            for source_file in case.source.iterdir():
                source_file.unlink()
            case.source.rmdir()

            batch = AuthorizedHandoffAdapter().normalize(
                {"handoff": handoff, "output_root": str(output)}
            )
            self.assertEqual(batch, validate_observation_batch(batch))
            self.assertEqual(
                "audience-frame-observation-batch-v1",
                batch["schema_version"],
            )
            self.assertEqual("eligible-cohort-member", batch["unit"])

            structural = next(
                item for item in handoff["outputs"]
                if item["route"] == "structural_frame"
            )
            (output / structural["path"]).write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "output hash mismatch"):
                AuthorizedHandoffAdapter().normalize(
                    {"handoff": handoff, "output_root": str(output)}
                )
        finally:
            case.tearDown()


if __name__ == "__main__":
    unittest.main()
