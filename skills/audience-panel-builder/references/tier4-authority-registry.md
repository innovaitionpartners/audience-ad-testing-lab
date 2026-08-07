# Tier 4 authority registry

Tier 4 preregistration sealing, claim-family construction, evaluation, claim
issuance, package building/validation, library access, reporting, and dashboard
display require a live trusted authority registry with schema
`panel-validation-authority-registry-v1`.

The registry is a closed canonical JSON document:

```json
{
  "schema_version": "panel-validation-authority-registry-v1",
  "registry_id": "innovaition-tier4-authority-v1",
  "entries": [
    {
      "authority_id": "validation-owner",
      "registration_id": "validation-q3",
      "approved_at": "2026-07-31T00:00:00Z",
      "registered_at": "2026-08-01T00:00:00Z",
      "authority_root_sha256": "sha256:<64 lowercase hex>",
      "authority_index_sha256": "sha256:<64 lowercase hex>",
      "design_evidence_sha256": "sha256:<64 lowercase hex>"
    }
  ],
  "registry_sha256": "sha256:<64 lowercase hex>",
  "registry_hmac_sha256": "sha256:<64 lowercase hex>"
}
```

Entries are unique and sorted by `(authority_id, registration_id)`.
`design_evidence_sha256` binds the complete decision-relevant
preregistration, including identity, chronology, panel and frozen surface,
claim scope, metrics, blocks, exact per-arm segment membership, holdout and
prior access, all analysis/tie/bootstrap rules, eligibility and segment rules,
multiplicity/Holm, interim looks, power, inventory weights, and approval
identity.

`registry_sha256` is calculated with both digest fields null.
`registry_hmac_sha256` is HMAC-SHA-256 over the canonical registry with only
the HMAC field null and the completed registry self-hash present.

The HMAC secret is one half of the out-of-band trust anchor. It must be
supplied through a protected key file or equivalent secret channel, must
contain at least 32 bytes, and must never be placed in the registry,
preregistration, validation package, evaluation, claim, command-line value,
logs, or reports. There is no default production secret.

For the file interface, "protected" is enforced: the path must be one
non-symlink regular file owned by the runtime user, with no group or world
permission bits. The reader pins the inode and rejects a file that changes
while it is read.

The other half is runtime-pinned: production code accepts only registry ID
`innovaition-tier4-authority-v1` and the SHA-256 fingerprint of its provisioned
secret. The secret fingerprint is safe to ship; the secret is not. Supplying a
self-consistent replacement registry and replacement HMAC key therefore fails
before HMAC verification. Changing the production authority requires a
reviewed runtime release that deliberately changes the pinned identity.

Every Tier 4 CLI requires:

- the authenticated registry file;
- the protected authority-secret file.

They verify the secret against the runtime-pinned identity, verify the
registry HMAC, mint a process-local private capability, and resolve exactly one
matching entry. Registration and evaluation additionally require the
authority-root and authority-index files so they can verify that those exact
bytes match the registry entry. Capabilities are never serialized.

Self-hashes remain useful integrity commitments, but they are never authority.
Every package read, registry lookup, report, and Tier 4 dashboard display
reauthenticates the embedded preregistration with the live process-local
capability. Claim-family construction performs the same check for every Holm
sibling.
