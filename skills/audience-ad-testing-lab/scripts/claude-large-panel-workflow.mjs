export const meta = {
  name: "audience-ad-progressive-panel",
  description: "Collect progressively exposed synthetic reactions and comparisons",
};

if (!args || typeof args !== "object") {
  throw new Error("Workflow args must be an object.");
}

const authenticatedJobsEnvelope = args.authenticated_jobs_envelope;
if (
  authenticatedJobsEnvelope !== undefined
  && (
    !authenticatedJobsEnvelope
    || typeof authenticatedJobsEnvelope !== "object"
    || Array.isArray(authenticatedJobsEnvelope)
    || Object.keys(authenticatedJobsEnvelope).sort().join(",") !== [
      "audience_allocation_subset",
      "audience_dispatch",
      "audience_run_claim",
      "method",
      "record_type",
      "study_id",
      "synthetic_replicate_jobs",
    ].sort().join(",")
  )
) {
  throw new Error("args.authenticated_jobs_envelope must be one exact v3 jobs envelope.");
}
const jobs = authenticatedJobsEnvelope?.synthetic_replicate_jobs ?? args.synthetic_replicate_jobs;
if (!Array.isArray(jobs) || jobs.length === 0) {
  throw new Error("the selected jobs input must be a non-empty array.");
}
const legacyV2OriginAuthority = args.legacy_v2_origin_authority;
if (
  authenticatedJobsEnvelope !== undefined
  && legacyV2OriginAuthority !== undefined
) {
  throw new Error("legacy v2 origin authority cannot downgrade authenticated v3 jobs.");
}
if (authenticatedJobsEnvelope === undefined) {
  const plainObject = value => (
    value !== null && typeof value === "object" && !Array.isArray(value)
  );
  const canonicalCompactJson = value => {
    if (Array.isArray(value)) {
      return `[${value.map(canonicalCompactJson).join(",")}]`;
    }
    if (plainObject(value)) {
      return `{${Object.keys(value).sort().map(
        key => `${JSON.stringify(key)}:${canonicalCompactJson(value[key])}`,
      ).join(",")}}`;
    }
    return JSON.stringify(value);
  };
  const sortedJsonValue = value => {
    if (Array.isArray(value)) {
      return value.map(sortedJsonValue);
    }
    if (plainObject(value)) {
      return Object.fromEntries(
        Object.keys(value).sort().map(
          key => [key, sortedJsonValue(value[key])],
        ),
      );
    }
    return value;
  };
  const canonicalEvidenceBytes = value => Buffer.from(
    `${JSON.stringify(sortedJsonValue(value), null, 2)}\n`,
    "utf8",
  );
  const sameKeys = (value, expected) => (
    plainObject(value)
    && Object.keys(value).sort().join(",") === [...expected].sort().join(",")
  );
  const expectedLegacyKeys = [
    "canonical_job_cores",
    "origin",
    "producer",
    "producer_version",
    "schema_version",
    "source_assignment_core",
    "source_dispatch_context",
    "source_manifest",
  ];
  const evidenceKeys = [
    "evidence_id",
    "produced_jobs",
    "schema_version",
    "source_assignment",
    "source_dispatch_context",
    "source_manifest",
    "source_package",
    "source_package_validation",
  ];
  const evidenceBindingKeys = ["byte_count", "path", "sha256"];
  const evidenceFilenames = {
    source_package: "audience-panel-package.zip",
    source_package_validation: "source-package-validation.json",
    source_assignment: "source-assignment.json",
    source_dispatch_context: "source-dispatch-context.json",
    source_manifest: "source-manifest.json",
    produced_jobs: "produced-jobs.json",
  };
  const v2PackageKeys = [
    "brief_byte_count",
    "brief_id",
    "brief_sha256",
    "package_manifest_byte_count",
    "package_manifest_sha256",
    "package_zip_byte_count",
    "package_zip_sha256",
    "panel_byte_count",
    "panel_id",
    "panel_sha256",
    "panel_version",
    "resolved_snapshot_path",
  ];
  const v3SourceFields = [
    "audience_allocation_fidelity",
    "audience_allocation_subset",
    "audience_dispatch",
    "audience_profile_rosters",
    "audience_run_claim",
  ];
  const originFailure = () => {
    throw new Error(
      "loose legacy v2 jobs require exact independent application-managed "
      + "producer evidence and origin authority; v3 jobs require "
      + "authenticated_jobs_envelope.",
    );
  };
  if (
    typeof legacyV2OriginAuthority !== "string"
    || legacyV2OriginAuthority.length === 0
    || typeof process === "undefined"
    || typeof process.getBuiltinModule !== "function"
    || typeof Buffer === "undefined"
  ) {
    originFailure();
  }
  let nodeFs;
  let nodePath;
  let nodeCrypto;
  let nodeChildProcess;
  let nodeOs;
  try {
    nodeFs = process.getBuiltinModule("node:fs");
    nodePath = process.getBuiltinModule("node:path");
    nodeCrypto = process.getBuiltinModule("node:crypto");
    nodeChildProcess = process.getBuiltinModule("node:child_process");
    nodeOs = process.getBuiltinModule("node:os");
  } catch {
    originFailure();
  }
  if (
    typeof nodeFs?.readFileSync !== "function"
    || typeof nodePath?.resolve !== "function"
    || typeof nodeCrypto?.createHash !== "function"
    || typeof nodeChildProcess?.spawnSync !== "function"
    || typeof nodeOs?.tmpdir !== "function"
  ) {
    originFailure();
  }
  const authorityPath = nodePath.resolve(legacyV2OriginAuthority);
  const requireReadOnlyFile = pathValue => {
    let metadata;
    try {
      metadata = nodeFs.lstatSync(pathValue);
    } catch {
      originFailure();
    }
    if (
      metadata.isSymbolicLink()
      || !metadata.isFile()
      || (metadata.mode & 0o222) !== 0
    ) {
      originFailure();
    }
    try {
      if (nodeFs.realpathSync(pathValue) !== pathValue) {
        originFailure();
      }
      return nodeFs.readFileSync(pathValue);
    } catch {
      originFailure();
    }
    return null;
  };
  const parseCanonicalJson = raw => {
    let value;
    try {
      value = JSON.parse(raw.toString("utf8"));
    } catch {
      originFailure();
    }
    if (!raw.equals(canonicalEvidenceBytes(value))) {
      originFailure();
    }
    return value;
  };
  if (
    !nodePath.isAbsolute(legacyV2OriginAuthority)
    || authorityPath !== legacyV2OriginAuthority
  ) {
    originFailure();
  }
  const authorityRaw = requireReadOnlyFile(authorityPath);
  const authority = parseCanonicalJson(authorityRaw);
  if (
    !sameKeys(authority, expectedLegacyKeys)
    || authority.schema_version !== "audience-jobs-producer-record-v2"
    || authority.origin !== "legacy_v2"
    || authority.producer !== "prepare-panel-jobs.py"
    || authority.producer_version !== "2.1.0"
  ) {
    originFailure();
  }
  const source = authority.source_assignment_core;
  const sourceManifest = authority.source_manifest;
  const sourceContextWithEvidence = authority.source_dispatch_context;
  const producerEvidence = sourceContextWithEvidence?.producer_evidence;
  if (
    !plainObject(source)
    || !plainObject(sourceContextWithEvidence)
    || !(sourceManifest === null || plainObject(sourceManifest))
    || !sameKeys(producerEvidence, evidenceKeys)
    || producerEvidence.schema_version
      !== "audience-jobs-producer-evidence-v1"
  ) {
    originFailure();
  }
  const recordName = nodePath.basename(authorityPath);
  const recordStem = nodePath.parse(recordName).name;
  const evidenceDirectory = nodePath.join(
    nodePath.dirname(authorityPath),
    `${recordStem}.evidence`,
  );
  let evidenceDirectoryMetadata;
  try {
    evidenceDirectoryMetadata = nodeFs.lstatSync(evidenceDirectory);
  } catch {
    originFailure();
  }
  if (
    evidenceDirectoryMetadata.isSymbolicLink()
    || !evidenceDirectoryMetadata.isDirectory()
    || (evidenceDirectoryMetadata.mode & 0o222) !== 0
  ) {
    originFailure();
  }
  try {
    if (nodeFs.realpathSync(evidenceDirectory) !== evidenceDirectory) {
      originFailure();
    }
  } catch {
    originFailure();
  }
  const evidenceBindings = {};
  const evidenceRaw = {};
  for (const [bindingName, filename] of Object.entries(evidenceFilenames)) {
    const binding = producerEvidence[bindingName];
    if (bindingName === "source_manifest" && sourceManifest === null) {
      if (binding !== null) {
        originFailure();
      }
      evidenceBindings[bindingName] = null;
      continue;
    }
    const expectedRelative = `${recordStem}.evidence/${filename}`;
    if (
      !sameKeys(binding, evidenceBindingKeys)
      || binding.path !== expectedRelative
      || nodePath.posix.isAbsolute(binding.path)
      || nodePath.posix.normalize(binding.path) !== binding.path
      || binding.path.includes("\\")
      || !/^[0-9a-f]{64}$/.test(binding.sha256)
      || !Number.isSafeInteger(binding.byte_count)
      || binding.byte_count < 1
    ) {
      originFailure();
    }
    const evidencePath = nodePath.join(
      nodePath.dirname(authorityPath),
      ...binding.path.split("/"),
    );
    if (nodePath.dirname(evidencePath) !== evidenceDirectory) {
      originFailure();
    }
    const raw = requireReadOnlyFile(evidencePath);
    const digest = nodeCrypto.createHash("sha256").update(raw).digest("hex");
    if (digest !== binding.sha256 || raw.length !== binding.byte_count) {
      originFailure();
    }
    evidenceBindings[bindingName] = binding;
    evidenceRaw[bindingName] = raw;
  }
  const evidenceIdentity = Object.fromEntries(
    Object.keys(evidenceFilenames).map(
      key => [key, evidenceBindings[key]],
    ),
  );
  if (
    !/^[0-9a-f]{64}$/.test(producerEvidence.evidence_id)
    || nodeCrypto.createHash("sha256")
      .update(canonicalEvidenceBytes(evidenceIdentity))
      .digest("hex") !== producerEvidence.evidence_id
  ) {
    originFailure();
  }
  const reopenedSource = parseCanonicalJson(evidenceRaw.source_assignment);
  const reopenedContext = parseCanonicalJson(
    evidenceRaw.source_dispatch_context,
  );
  const reopenedManifest = sourceManifest === null
    ? null
    : parseCanonicalJson(evidenceRaw.source_manifest);
  const reopenedProducedJobs = parseCanonicalJson(evidenceRaw.produced_jobs);
  const packagePreflight = parseCanonicalJson(
    evidenceRaw.source_package_validation,
  );
  const sourceContext = {...sourceContextWithEvidence};
  delete sourceContext.producer_evidence;
  if (
    canonicalCompactJson(reopenedSource) !== canonicalCompactJson(source)
    || canonicalCompactJson(reopenedContext)
      !== canonicalCompactJson(sourceContext)
    || canonicalCompactJson(reopenedManifest)
      !== canonicalCompactJson(sourceManifest)
  ) {
    originFailure();
  }
  const sourcePackage = source?.audience_package
    ?? sourceManifest?.audience_package;
  if (
    source?.audience_package !== undefined
    && sourceManifest?.audience_package !== undefined
    && canonicalCompactJson(source.audience_package)
      !== canonicalCompactJson(sourceManifest.audience_package)
  ) {
    originFailure();
  }
  const packagePreflightKeys = [
    "brief_id",
    "brief_sha256",
    "package_manifest_byte_count",
    "package_manifest_sha256",
    "package_zip_byte_count",
    "package_zip_sha256",
    "panel_id",
    "panel_sha256",
    "panel_version",
    "schema_version",
    "status",
  ];
  const hashBytes = raw => nodeCrypto.createHash("sha256")
    .update(raw)
    .digest("hex");
  const crcTable = Array.from({length: 256}, (_, seed) => {
    let value = seed;
    for (let bit = 0; bit < 8; bit += 1) {
      value = (value >>> 1) ^ (
        (value & 1) === 1 ? 0xedb88320 : 0
      );
    }
    return value >>> 0;
  });
  const crc32 = raw => {
    let checksum = 0xffffffff;
    for (const byte of raw) {
      checksum = crcTable[(checksum ^ byte) & 0xff]
        ^ (checksum >>> 8);
    }
    return (checksum ^ 0xffffffff) >>> 0;
  };
  const validateStoredV2Archive = raw => {
    const archiveNames = [
      "persona-research-brief.json",
      "saved-audience-panel.json",
      "research-sources.csv",
      "audience-research-report.html",
      "README.txt",
      "package-manifest.json",
    ];
    try {
      if (
        raw.length < 22
        || raw.length > 75 * 1024 * 1024
        || raw.readUInt32LE(raw.length - 22) !== 0x06054b50
      ) {
        originFailure();
      }
      const endOffset = raw.length - 22;
      const disk = raw.readUInt16LE(endOffset + 4);
      const centralDisk = raw.readUInt16LE(endOffset + 6);
      const diskEntries = raw.readUInt16LE(endOffset + 8);
      const totalEntries = raw.readUInt16LE(endOffset + 10);
      const centralSize = raw.readUInt32LE(endOffset + 12);
      const centralOffset = raw.readUInt32LE(endOffset + 16);
      const commentLength = raw.readUInt16LE(endOffset + 20);
      if (
        disk !== 0
        || centralDisk !== 0
        || diskEntries !== archiveNames.length
        || totalEntries !== archiveNames.length
        || commentLength !== 0
        || centralOffset + centralSize !== endOffset
      ) {
        originFailure();
      }
      const members = {};
      let centralCursor = centralOffset;
      let localCursor = 0;
      for (const expectedName of archiveNames) {
        if (
          centralCursor + 46 > endOffset
          || raw.readUInt32LE(centralCursor) !== 0x02014b50
        ) {
          originFailure();
        }
        const flags = raw.readUInt16LE(centralCursor + 8);
        const compression = raw.readUInt16LE(centralCursor + 10);
        const centralCrc = raw.readUInt32LE(centralCursor + 16);
        const compressedSize = raw.readUInt32LE(centralCursor + 20);
        const uncompressedSize = raw.readUInt32LE(centralCursor + 24);
        const filenameLength = raw.readUInt16LE(centralCursor + 28);
        const extraLength = raw.readUInt16LE(centralCursor + 30);
        const memberCommentLength = raw.readUInt16LE(centralCursor + 32);
        const diskStart = raw.readUInt16LE(centralCursor + 34);
        const localOffset = raw.readUInt32LE(centralCursor + 42);
        const centralEnd = centralCursor + 46 + filenameLength
          + extraLength + memberCommentLength;
        if (centralEnd > endOffset) {
          originFailure();
        }
        const name = raw.subarray(
          centralCursor + 46,
          centralCursor + 46 + filenameLength,
        ).toString("utf8");
        if (
          name !== expectedName
          || flags !== 0
          || compression !== 0
          || compressedSize !== uncompressedSize
          || uncompressedSize > 25 * 1024 * 1024
          || extraLength !== 0
          || memberCommentLength !== 0
          || diskStart !== 0
          || localOffset !== localCursor
          || localOffset + 30 > centralOffset
          || raw.readUInt32LE(localOffset) !== 0x04034b50
        ) {
          originFailure();
        }
        const localFlags = raw.readUInt16LE(localOffset + 6);
        const localCompression = raw.readUInt16LE(localOffset + 8);
        const localCrc = raw.readUInt32LE(localOffset + 14);
        const localCompressedSize = raw.readUInt32LE(localOffset + 18);
        const localUncompressedSize = raw.readUInt32LE(localOffset + 22);
        const localNameLength = raw.readUInt16LE(localOffset + 26);
        const localExtraLength = raw.readUInt16LE(localOffset + 28);
        const localNameEnd = localOffset + 30 + localNameLength;
        const dataStart = localNameEnd + localExtraLength;
        const dataEnd = dataStart + compressedSize;
        const localName = raw.subarray(
          localOffset + 30,
          localNameEnd,
        ).toString("utf8");
        if (
          localName !== name
          || localFlags !== flags
          || localCompression !== compression
          || localCrc !== centralCrc
          || localCompressedSize !== compressedSize
          || localUncompressedSize !== uncompressedSize
          || localExtraLength !== 0
          || dataEnd > centralOffset
        ) {
          originFailure();
        }
        const member = raw.subarray(dataStart, dataEnd);
        if (crc32(member) !== centralCrc) {
          originFailure();
        }
        members[name] = member;
        localCursor = dataEnd;
        centralCursor = centralEnd;
      }
      if (
        localCursor !== centralOffset
        || centralCursor !== endOffset
      ) {
        originFailure();
      }
      const manifestRaw = members["package-manifest.json"];
      const manifest = JSON.parse(manifestRaw.toString("utf8"));
      if (
        !manifestRaw.equals(Buffer.from(
          `${canonicalCompactJson(manifest)}\n`,
          "utf8",
        ))
        || !sameKeys(manifest, [
          "brief_id",
          "files",
          "generated_at",
          "generator_version",
          "panel_id",
          "panel_version",
          "schema_version",
        ])
        || manifest.schema_version !== "audience-panel-package-v2"
        || manifest.generator_version !== "1.0.0"
        || !plainObject(manifest.files)
      ) {
        originFailure();
      }
      const memberNames = archiveNames.slice(0, -1);
      if (
        Object.keys(manifest.files).sort().join(",")
        !== [...memberNames].sort().join(",")
      ) {
        originFailure();
      }
      for (const memberName of memberNames) {
        const binding = manifest.files[memberName];
        const member = members[memberName];
        if (
          !sameKeys(binding, ["byte_count", "path", "sha256"])
          || binding.path !== memberName
          || binding.byte_count !== member.length
          || binding.sha256 !== hashBytes(member)
        ) {
          originFailure();
        }
      }
      const briefRaw = members["persona-research-brief.json"];
      const panelRaw = members["saved-audience-panel.json"];
      const brief = JSON.parse(briefRaw.toString("utf8"));
      const panel = JSON.parse(panelRaw.toString("utf8"));
      if (
        brief.schema_version !== "audience-research-brief-v2"
        || panel.schema_version !== "saved-audience-panel-v2"
        || brief.brief_id !== manifest.brief_id
        || panel.panel_id !== manifest.panel_id
        || panel.version !== manifest.panel_version
        || panel.persona_research?.brief_id !== brief.brief_id
      ) {
        originFailure();
      }
      return {
        briefRaw,
        manifestRaw,
        panelRaw,
      };
    } catch {
      originFailure();
    }
    return null;
  };
  const sourcePackageRaw = evidenceRaw.source_package;
  const sourcePackageDigest = hashBytes(sourcePackageRaw);
  if (
    !sameKeys(sourcePackage, v2PackageKeys)
    || !sameKeys(packagePreflight, packagePreflightKeys)
    || packagePreflight.schema_version !== "audience-panel-package-v2"
    || packagePreflight.status !== "valid"
    || sourcePackageDigest !== sourcePackage.package_zip_sha256
    || sourcePackageRaw.length !== sourcePackage.package_zip_byte_count
    || [
      "brief_id",
      "brief_sha256",
      "package_manifest_byte_count",
      "package_manifest_sha256",
      "package_zip_byte_count",
      "package_zip_sha256",
      "panel_id",
      "panel_sha256",
      "panel_version",
    ].some(key => packagePreflight[key] !== sourcePackage[key])
  ) {
    originFailure();
  }
  const validatedArchive = validateStoredV2Archive(sourcePackageRaw);
  if (
    hashBytes(validatedArchive.manifestRaw)
      !== sourcePackage.package_manifest_sha256
    || validatedArchive.manifestRaw.length
      !== sourcePackage.package_manifest_byte_count
    || hashBytes(validatedArchive.briefRaw)
      !== sourcePackage.brief_sha256
    || validatedArchive.briefRaw.length
      !== sourcePackage.brief_byte_count
    || hashBytes(validatedArchive.panelRaw)
      !== sourcePackage.panel_sha256
    || validatedArchive.panelRaw.length
      !== sourcePackage.panel_byte_count
  ) {
    originFailure();
  }
  const canonicalJobs = authority.canonical_job_cores;
  if (
    !plainObject(reopenedProducedJobs)
    || Object.keys(reopenedProducedJobs).sort().join(",") !== [
      "method",
      "record_type",
      "study_id",
      "synthetic_replicate_jobs",
    ].sort().join(",")
    || canonicalCompactJson(reopenedProducedJobs.synthetic_replicate_jobs)
      !== canonicalCompactJson(canonicalJobs)
  ) {
    originFailure();
  }
  const sourceStudyId = source?.study_id ?? sourceManifest?.study_id;
  const sourceMethod = source?.method ?? sourceManifest?.method;
  const recordType = sourceContext?.record_type;
  const canonicalIds = Array.isArray(canonicalJobs)
    ? canonicalJobs.map(item => item?.synthetic_replicate_id)
    : [];
  const selectedIds = jobs.map(item => item?.synthetic_replicate_id);
  const selectedCanonical = Array.isArray(canonicalJobs)
    ? canonicalJobs.filter(
      item => selectedIds.includes(item?.synthetic_replicate_id),
    )
    : [];
  const sourceContainsV3 = [source, sourceManifest || {}].some(
    item => plainObject(item) && v3SourceFields.some(field => field in item),
  );
  const jobContainsV3 = Array.isArray(canonicalJobs) && canonicalJobs.some(
    item => plainObject(item) && (
      "audience_slot_id" in item
      || "profile_snapshot_sha256" in item
      || v3SourceFields.some(field => field in item)
    ),
  );
  let upstreamBindingValid = false;
  if (recordType === "screening_response") {
    const upstream = source?.assignment?.synthetic_replicate_jobs;
    upstreamBindingValid = Array.isArray(upstream) && canonicalJobs?.every(
      job => upstream.some(core => (
        core?.synthetic_replicate_id === job.synthetic_replicate_id
        && canonicalCompactJson(core?.variation_ids)
          === canonicalCompactJson(job.variation_ids)
        && canonicalCompactJson(core?.shown_order)
          === canonicalCompactJson(job.shown_order)
      )),
    );
  } else if (recordType === "boundary_response") {
    const upstream = source?.boundary_plan?.predeclared_pair_assignments;
    upstreamBindingValid = Array.isArray(upstream) && canonicalJobs?.every(
      job => upstream.some(core => (
        core?.pair_assignment_id === job.synthetic_replicate_id
        && canonicalCompactJson(core?.variation_ids)
          === canonicalCompactJson(job.variation_ids)
        && (
          job.boundary_wave === undefined
          || (core?.wave ?? core?.boundary_wave) === job.boundary_wave
        )
      )),
    );
  } else if (recordType === "finalist_response") {
    const approved = source?.approved_finalist_ids;
    upstreamBindingValid = Array.isArray(approved) && canonicalJobs?.every(
      job => canonicalCompactJson(job.variation_ids)
        === canonicalCompactJson(approved),
    );
  }
  if (
    sourceContainsV3
    || jobContainsV3
    || sourceContext.study_id !== sourceStudyId
    || sourceStudyId !== jobs[0]?.study_id
    || sourceMethod !== jobs[0]?.method
    || recordType !== jobs[0]?.record_type
    || reopenedProducedJobs.study_id !== sourceStudyId
    || reopenedProducedJobs.method !== sourceMethod
    || reopenedProducedJobs.record_type !== recordType
    || !Array.isArray(canonicalJobs)
    || canonicalJobs.length === 0
    || new Set(canonicalIds).size !== canonicalIds.length
    || canonicalCompactJson(selectedCanonical) !== canonicalCompactJson(jobs)
    || !upstreamBindingValid
  ) {
    originFailure();
  }

  const semanticValidatorRelativePaths = [
    [
      "skills",
      "audience-ad-testing-lab",
      "scripts",
      "validate-workflow-v2-origin.py",
    ],
    ["scripts", "validate-workflow-v2-origin.py"],
  ];
  const semanticValidatorSha256 =
    "c416af5fb9c38e58ec26fe59b709a3d82dea13dcf8fca54bbeaf032feaecc2a3";
  const semanticBundleFilename = "workflow-v2-semantic-bundle.b85";
  const semanticBundleSha256 =
    "83cc88620f415d6ff9c0974b25f18342cae21b423fdaaafc9bce90052b6302e5";
  const runtimeRoots = [];
  let runtimeRoot;
  try {
    runtimeRoot = nodePath.resolve(process.cwd());
  } catch {
    originFailure();
  }
  for (let depth = 0; depth < 8; depth += 1) {
    runtimeRoots.push(runtimeRoot);
    const parent = nodePath.dirname(runtimeRoot);
    if (parent === runtimeRoot) break;
    runtimeRoot = parent;
  }
  let semanticValidatorPath = null;
  let semanticBundlePath = null;
  let semanticValidatorRaw = null;
  let semanticBundleRaw = null;
  for (const root of runtimeRoots) {
    for (const relativeParts of semanticValidatorRelativePaths) {
      const candidate = nodePath.join(root, ...relativeParts);
      const bundleCandidate = nodePath.join(
        nodePath.dirname(candidate),
        semanticBundleFilename,
      );
      try {
        const metadata = nodeFs.lstatSync(candidate);
        const bundleMetadata = nodeFs.lstatSync(bundleCandidate);
        if (
          metadata.isSymbolicLink()
          || !metadata.isFile()
          || bundleMetadata.isSymbolicLink()
          || !bundleMetadata.isFile()
        ) {
          continue;
        }
        const realCandidate = nodeFs.realpathSync(candidate);
        const realBundleCandidate = nodeFs.realpathSync(bundleCandidate);
        if (
          nodePath.dirname(realCandidate)
          !== nodePath.dirname(realBundleCandidate)
        ) {
          continue;
        }
        const raw = nodeFs.readFileSync(realCandidate);
        const bundleRaw = nodeFs.readFileSync(realBundleCandidate);
        if (
          hashBytes(raw) !== semanticValidatorSha256
          || hashBytes(bundleRaw) !== semanticBundleSha256
        ) {
          continue;
        }
        semanticValidatorPath = realCandidate;
        semanticBundlePath = realBundleCandidate;
        semanticValidatorRaw = raw;
        semanticBundleRaw = bundleRaw;
        break;
      } catch {
        continue;
      }
    }
    if (semanticValidatorPath !== null) break;
  }
  if (
    semanticValidatorPath === null
    || semanticBundlePath === null
    || !Buffer.isBuffer(semanticValidatorRaw)
    || !Buffer.isBuffer(semanticBundleRaw)
  ) {
    originFailure();
  }

  const semanticCandidate = {
    study_id: jobs[0].study_id,
    method: jobs[0].method,
    record_type: jobs[0].record_type,
    synthetic_replicate_jobs: jobs,
  };
  const semanticCandidateRaw = canonicalEvidenceBytes(semanticCandidate);
  let semanticTemporaryDirectory = null;
  let semanticCandidatePath = null;
  let semanticStagedValidatorPath = null;
  let semanticStagedBundlePath = null;
  let semanticExecution = null;
  let semanticCleanupSucceeded = true;
  try {
    const temporaryBase = nodeFs.realpathSync(nodeOs.tmpdir());
    semanticTemporaryDirectory = nodeFs.mkdtempSync(
      nodePath.join(temporaryBase, "audience-v2-semantic-"),
    );
    nodeFs.chmodSync(semanticTemporaryDirectory, 0o700);
    semanticStagedValidatorPath = nodePath.join(
      semanticTemporaryDirectory,
      "validate-workflow-v2-origin.py",
    );
    nodeFs.writeFileSync(
      semanticStagedValidatorPath,
      semanticValidatorRaw,
      {flag: "wx", mode: 0o600},
    );
    nodeFs.chmodSync(semanticStagedValidatorPath, 0o400);
    semanticStagedBundlePath = nodePath.join(
      semanticTemporaryDirectory,
      semanticBundleFilename,
    );
    nodeFs.writeFileSync(
      semanticStagedBundlePath,
      semanticBundleRaw,
      {flag: "wx", mode: 0o600},
    );
    nodeFs.chmodSync(semanticStagedBundlePath, 0o400);
    semanticCandidatePath = nodePath.join(
      semanticTemporaryDirectory,
      "candidate-jobs.json",
    );
    nodeFs.writeFileSync(
      semanticCandidatePath,
      semanticCandidateRaw,
      {flag: "wx", mode: 0o600},
    );
    nodeFs.chmodSync(semanticCandidatePath, 0o400);
    semanticExecution = nodeChildProcess.spawnSync(
      "python3",
      [
        "-I",
        "-S",
        semanticStagedValidatorPath,
        "--candidate-jobs",
        semanticCandidatePath,
        "--producer-record",
        authorityPath,
      ],
      {
        cwd: semanticTemporaryDirectory,
        encoding: "buffer",
        maxBuffer: 64 * 1024,
        timeout: 30000,
        windowsHide: true,
      },
    );
  } catch {
    originFailure();
  } finally {
    for (const stagedPath of [
      semanticCandidatePath,
      semanticStagedValidatorPath,
      semanticStagedBundlePath,
    ]) {
      if (stagedPath === null) continue;
      try {
        nodeFs.chmodSync(stagedPath, 0o600);
        nodeFs.unlinkSync(stagedPath);
      } catch {
        semanticCleanupSucceeded = false;
      }
    }
    if (semanticTemporaryDirectory !== null) {
      try {
        nodeFs.rmdirSync(semanticTemporaryDirectory);
      } catch {
        semanticCleanupSucceeded = false;
      }
    }
  }
  if (
    !semanticCleanupSucceeded
    || semanticExecution === null
    || semanticExecution.error !== undefined
    || semanticExecution.signal !== null
    || semanticExecution.status !== 0
    || !Buffer.isBuffer(semanticExecution.stdout)
    || !Buffer.isBuffer(semanticExecution.stderr)
    || semanticExecution.stderr.length !== 0
  ) {
    originFailure();
  }
  const semanticVerdict = parseCanonicalJson(semanticExecution.stdout);
  if (
    !sameKeys(semanticVerdict, [
      "candidate_jobs_sha256",
      "producer_record_sha256",
      "schema_version",
      "status",
    ])
    || semanticVerdict.schema_version
      !== "legacy-v2-workflow-semantic-preflight-v1"
    || semanticVerdict.status !== "valid"
    || semanticVerdict.candidate_jobs_sha256
      !== hashBytes(semanticCandidateRaw)
    || semanticVerdict.producer_record_sha256 !== hashBytes(authorityRaw)
  ) {
    originFailure();
  }
}
if (!args.reaction_schema || typeof args.reaction_schema !== "object") {
  throw new Error("args.reaction_schema is required.");
}
if (!args.comparison_schema || typeof args.comparison_schema !== "object") {
  throw new Error("args.comparison_schema is required.");
}

const recordTypes = new Set([
  "screening_response",
  "boundary_response",
  "finalist_response",
]);
const methods = new Set(["complete_exposure", "partial_exposure_maxdiff"]);
const reactionProtocols = new Set([
  "progressive_reveal",
  "reflective_reaction_caveat",
]);
const rubricKeys = [
  "comprehension",
  "relevance",
  "credibility",
  "offer_appeal",
  "motivation",
  "friction",
  "attention_potential",
  "overall",
];

const isObject = value => value !== null && typeof value === "object" && !Array.isArray(value);
const isString = value => typeof value === "string" && value.trim().length > 0;
const sameArray = (left, right) => (
  Array.isArray(left)
  && Array.isArray(right)
  && left.length === right.length
  && left.every((value, index) => value === right[index])
);
const sameSet = (left, right) => (
  Array.isArray(left)
  && Array.isArray(right)
  && left.length === right.length
  && new Set(left).size === left.length
  && new Set(right).size === right.length
  && left.every(value => right.includes(value))
);
const cloneJson = value => (
  value === undefined ? null : JSON.parse(JSON.stringify(value))
);
const deepFreeze = value => {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.values(value).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
};
const reactionKey = (job, position) => `${job.dispatch_id}:reaction:${position + 1}`;

const jobErrors = job => {
  const errors = [];
  if (!isObject(job)) return ["job must be an object"];
  for (const field of [
    "study_id",
    "response_id",
    "synthetic_replicate_id",
    "dispatch_id",
    "persona_archetype_id",
    "segment_id",
  ]) {
    if (!isString(job[field])) errors.push(`${field} must be a non-empty string`);
  }
  if ("context_stratum_id" in job && !isString(job.context_stratum_id)) {
    errors.push("context_stratum_id must be a non-empty string when supplied");
  }
  const v3IdentityFields = [
    "audience_slot_id",
    "grounded_profile_id",
    "profile_snapshot_sha256",
  ];
  const presentV3IdentityFields = v3IdentityFields.filter(field => field in job);
  const hasV3IdentityMarker =
    "audience_slot_id" in job || "profile_snapshot_sha256" in job;
  if (
    hasV3IdentityMarker
    && presentV3IdentityFields.length !== v3IdentityFields.length
  ) {
    errors.push("v3 audience identity fields must be supplied together");
  }
  if (hasV3IdentityMarker && presentV3IdentityFields.length === v3IdentityFields.length) {
    if (!isString(job.audience_slot_id) || job.audience_slot_id !== job.synthetic_replicate_id) {
      errors.push("audience_slot_id must exactly match synthetic_replicate_id");
    }
    if (!isString(job.grounded_profile_id)) {
      errors.push("grounded_profile_id must be a non-empty string");
    }
    if (
      typeof job.profile_snapshot_sha256 !== "string"
      || !/^sha256:[0-9a-f]{64}$/.test(job.profile_snapshot_sha256)
    ) {
      errors.push("profile_snapshot_sha256 must be a prefixed SHA-256");
    }
  }
  if (!recordTypes.has(job.record_type)) errors.push(`unsupported record_type: ${job.record_type}`);
  if (!methods.has(job.method)) errors.push("method must be complete_exposure or partial_exposure_maxdiff");
  if (!isObject(job.profile_snapshot) || Object.keys(job.profile_snapshot).length === 0) {
    errors.push("profile_snapshot must be a non-empty object");
  }
  if (!Array.isArray(job.context_attribute_provenance) || job.context_attribute_provenance.length === 0) {
    errors.push("context_attribute_provenance must be a non-empty array");
  } else {
    for (const [index, item] of job.context_attribute_provenance.entries()) {
      const evidence = item?.source_evidence ?? item?.evidence_ids;
      if (
        !isObject(item)
        || !isString(item.attribute ?? item.name)
        || !isString(item.value)
        || !["observed", "estimated", "experimental"].includes(item.status)
        || !Array.isArray(evidence)
        || evidence.length === 0
        || !evidence.every(isString)
      ) {
        errors.push(`context_attribute_provenance[${index}] is invalid`);
      }
    }
  }
  if (!["isolated", "shared_context_fallback"].includes(job.worker_context_isolation)) {
    errors.push("worker_context_isolation is invalid");
  }
  if (job.human_sample_independence !== false) {
    errors.push("human_sample_independence must be false");
  }
  if (
    !Array.isArray(job.variation_ids)
    || job.variation_ids.length === 0
    || !job.variation_ids.every(isString)
    || new Set(job.variation_ids).size !== job.variation_ids.length
  ) {
    errors.push("variation_ids must contain unique non-empty strings");
  }
  if (
    !Array.isArray(job.shown_order)
    || !sameSet(job.shown_order, job.variation_ids)
    || new Set(job.shown_order).size !== job.shown_order.length
  ) {
    errors.push("shown_order must be an exact permutation of variation_ids");
  }
  if (!isObject(job.blind_labels) || !sameSet(Object.keys(job.blind_labels), job.variation_ids)) {
    errors.push("blind_labels keys must exactly match variation_ids");
  } else if (
    !Object.values(job.blind_labels).every(isString)
    || new Set(Object.values(job.blind_labels)).size !== job.variation_ids.length
  ) {
    errors.push("blind_labels values must be unique non-empty strings");
  }
  if (
    job.record_type === "screening_response"
    && job.method === "partial_exposure_maxdiff"
    && job.variation_ids?.length !== 4
  ) {
    errors.push("partial_exposure_maxdiff screening jobs must assign exactly four variations");
  }
  if (
    job.record_type === "screening_response"
    && job.method === "complete_exposure"
    && (!Array.isArray(job.variation_ids) || job.variation_ids.length < 2 || job.variation_ids.length > 6)
  ) {
    errors.push("complete_exposure screening jobs must assign between two and six variations");
  }
  if (job.record_type === "boundary_response" && job.variation_ids?.length !== 2) {
    errors.push("boundary jobs must assign exactly two variations");
  }
  if (job.record_type === "boundary_response" && job.method !== "partial_exposure_maxdiff") {
    errors.push("boundary jobs require partial_exposure_maxdiff");
  }
  if (
    job.record_type === "finalist_response"
    && (!Array.isArray(job.variation_ids) || job.variation_ids.length < 2 || job.variation_ids.length > 6)
  ) {
    errors.push("finalist jobs must assign between two and six variations");
  }
  if (!reactionProtocols.has(job.reaction_protocol)) errors.push("reaction_protocol is invalid");
  if (
    !Array.isArray(job.reaction_prompts)
    || !job.reaction_prompts.every(isString)
    || job.reaction_prompts.length !== job.shown_order?.length
  ) {
    errors.push("reaction_prompts must contain one rendered prompt per shown creative");
  }
  if (!isString(job.comparison_prompt)) errors.push("comparison_prompt is required");
  if ("panelist_reviews" in job || "prompt" in job) {
    errors.push("obsolete one-shot review fields are not allowed");
  }
  return errors;
};

const seenReplicateIds = new Set();
const seenDispatchIds = new Set();
const seenResponseIds = new Set();
for (const [index, job] of jobs.entries()) {
  const errors = jobErrors(job);
  if (errors.length > 0) {
    throw new Error(`Invalid synthetic_replicate_jobs[${index}]: ${errors.join("; ")}`);
  }
  if (seenReplicateIds.has(job.synthetic_replicate_id)) {
    throw new Error(`Duplicate synthetic_replicate_id: ${job.synthetic_replicate_id}`);
  }
  if (seenDispatchIds.has(job.dispatch_id)) {
    throw new Error(`Duplicate dispatch_id: ${job.dispatch_id}`);
  }
  if (seenResponseIds.has(job.response_id)) {
    throw new Error(`Duplicate response_id: ${job.response_id}`);
  }
  seenReplicateIds.add(job.synthetic_replicate_id);
  seenDispatchIds.add(job.dispatch_id);
  seenResponseIds.add(job.response_id);
}
const carriesV3AudienceIdentity = jobs.some(job => "audience_slot_id" in job);
if (carriesV3AudienceIdentity && authenticatedJobsEnvelope === undefined) {
  throw new Error(
    "v3 jobs require args.authenticated_jobs_envelope; an extracted jobs array is not dispatch authority.",
  );
}
if (authenticatedJobsEnvelope !== undefined) {
  if (!jobs.every(job => "audience_slot_id" in job)) {
    throw new Error("authenticated v3 jobs envelope must contain only v3 audience-bound jobs.");
  }
  for (const field of ["study_id", "method", "record_type"]) {
    if (jobs.some(job => job[field] !== authenticatedJobsEnvelope[field])) {
      throw new Error(`authenticated v3 jobs envelope ${field} must match every job.`);
    }
  }
}

const reactionErrors = (job, position, result) => {
  const errors = [];
  if (!isObject(result)) return ["return must be an object"];
  const variationId = job.shown_order[position];
  if (result.variation_id !== variationId) {
    errors.push("variation_id does not match the revealed creative");
  }
  if (result.display_label_seen !== job.blind_labels[variationId]) {
    errors.push("display_label_seen does not match the revealed blind label");
  }
  if (result.position_seen !== position + 1) {
    errors.push("position_seen does not match the reveal order");
  }
  const expectedLabel = job.reaction_protocol === "progressive_reveal" ? "immediate" : "reflective";
  if (result.reaction_label !== expectedLabel) {
    errors.push(`reaction_label must be ${expectedLabel}`);
  }
  for (const field of [
    "immediate_reaction",
    "noticed_or_understood_first",
    "strongest_positive_signal",
    "strongest_negative_signal",
  ]) {
    if (!isString(result[field])) errors.push(`${field} is required`);
  }
  if (!["judged", "unable_to_judge"].includes(result.judgment_status)) {
    errors.push("judgment_status is invalid");
  }
  return errors;
};

const choiceSource = providerReturnId => ({
  provider_return_id: providerReturnId,
  capture: "verbatim_provider_return",
});

const comparisonErrors = (job, reactions, result) => {
  if (!isObject(result)) return ["return must be an object"];
  const errors = [];
  const allJudged = reactions.every(reaction => reaction.judgment_status === "judged");
  if (job.record_type === "screening_response" && job.method === "partial_exposure_maxdiff") {
    const choice = result.comparative_choice;
    if (!isObject(choice)) return ["comparative_choice must be an object"];
    const statuses = new Set(["best_worst", "no_meaningful_difference", "unable_to_judge"]);
    if (!statuses.has(choice.status)) errors.push("comparative_choice.status is invalid");
    let usableChoice = false;
    if (choice.status === "best_worst") {
      if (!job.variation_ids.includes(choice.best_variation_id)) {
        errors.push("best_variation_id is not assigned");
      }
      if (!job.variation_ids.includes(choice.weakest_variation_id)) {
        errors.push("weakest_variation_id is not assigned");
      }
      if (choice.best_variation_id === choice.weakest_variation_id) {
        errors.push("best and weakest variation IDs must differ");
      }
      if (!isString(choice.best_reason)) errors.push("best_reason is required");
      if (!isString(choice.weakest_reason)) errors.push("weakest_reason is required");
      usableChoice = (
        job.variation_ids.includes(choice.best_variation_id)
        && job.variation_ids.includes(choice.weakest_variation_id)
        && choice.best_variation_id !== choice.weakest_variation_id
      );
    } else {
      for (const field of ["best_variation_id", "weakest_variation_id"]) {
        if (![null, undefined, ""].includes(choice[field])) {
          errors.push(`comparative_choice.${field} must be empty for ${choice.status}`);
        }
      }
    }
    if (result.usable_maxdiff_block !== (allJudged && usableChoice)) {
      errors.push(`usable_maxdiff_block must be ${allJudged && usableChoice}`);
    }
  } else if (job.record_type === "screening_response" && job.method === "complete_exposure") {
    const evaluation = result.complete_set_evaluation;
    if (!isObject(evaluation)) return ["complete_set_evaluation must be an object"];
    const statuses = new Set(["ranked", "unable_to_judge"]);
    if (!statuses.has(evaluation.status)) errors.push("complete_set_evaluation.status is invalid");
    let usableEvaluation = false;
    if (evaluation.status === "ranked") {
      if (!sameSet(evaluation.preference_ranking, job.variation_ids)) {
        errors.push("complete_set_evaluation.preference_ranking must match the complete set");
      } else usableEvaluation = true;
    } else if (![null, undefined, ""].includes(evaluation.preference_ranking)
      && !(Array.isArray(evaluation.preference_ranking) && evaluation.preference_ranking.length === 0)) {
      errors.push("complete_set_evaluation.preference_ranking must be empty when unable to judge");
    }
    if (result.usable_complete_exposure_observation !== (allJudged && usableEvaluation)) {
      errors.push(`usable_complete_exposure_observation must be ${allJudged && usableEvaluation}`);
    }
  } else if (job.record_type === "boundary_response") {
    const choice = result.pairwise_choice;
    if (!isObject(choice)) return ["pairwise_choice must be an object"];
    const statuses = new Set(["first_preferred", "second_preferred", "tie", "unable_to_judge"]);
    if (!statuses.has(choice.status)) errors.push("pairwise_choice.status is invalid");
    let usableChoice = false;
    if (choice.status === "first_preferred") {
      if (choice.preferred_variation_id !== job.shown_order[0]) {
        errors.push("preferred_variation_id must match the first shown creative");
      } else usableChoice = true;
    } else if (choice.status === "second_preferred") {
      if (choice.preferred_variation_id !== job.shown_order[1]) {
        errors.push("preferred_variation_id must match the second shown creative");
      } else usableChoice = true;
    } else if (choice.status === "tie") {
      if (![null, undefined, ""].includes(choice.preferred_variation_id)) {
        errors.push("preferred_variation_id must be empty for a tie");
      }
      usableChoice = true;
    } else if (choice.status === "unable_to_judge") {
      if (![null, undefined, ""].includes(choice.preferred_variation_id)) {
        errors.push("preferred_variation_id must be empty when unable to judge");
      }
    }
    if (!isString(choice.reason)) errors.push("pairwise_choice.reason is required");
    if (result.usable_pairwise_observation !== (allJudged && usableChoice)) {
      errors.push(`usable_pairwise_observation must be ${allJudged && usableChoice}`);
    }
  } else if (job.record_type === "finalist_response") {
    const assessments = result.finalist_assessments;
    if (!Array.isArray(assessments) || assessments.length !== reactions.length) {
      return ["finalist_assessments must contain one record per frozen reaction"];
    }
    for (const [index, assessment] of assessments.entries()) {
      const prefix = `finalist_assessments[${index}]`;
      if (!isObject(assessment)) {
        errors.push(`${prefix} must be an object`);
        continue;
      }
      if (assessment.variation_id !== job.shown_order[index]) {
        errors.push(`${prefix}.variation_id does not match shown_order`);
      }
      if (!isObject(assessment.rubric_scores)) {
        errors.push(`${prefix}.rubric_scores must be an object`);
      } else {
        for (const key of rubricKeys) {
          const score = assessment.rubric_scores[key];
          if (!Number.isInteger(score) || score < 1 || score > 5) {
            errors.push(`${prefix}.rubric_scores.${key} must be a whole number from 1-5`);
          }
        }
      }
      if (!Array.isArray(assessment.feedback) || !assessment.feedback.every(isString)) {
        errors.push(`${prefix}.feedback must be an array of strings`);
      }
    }
    if (!sameSet(result.final_preference_ranking, job.variation_ids)) {
      errors.push("final_preference_ranking must be an exact permutation of assigned variations");
    }
    if (!allJudged) errors.push("finalist reviews must be judged before final ranking");
  }
  return errors;
};

const rawProviderReturns = [];
const rejectedAttempts = [];
const validationFailures = new Map();

const recordProviderReturn = ({ job, position }, stage, attemptNumber, result, errors) => {
  const stageSuffix = stage === "reaction" ? `reaction-${position + 1}` : "comparison";
  const providerReturnId = `${job.dispatch_id}-${stageSuffix}-attempt-${attemptNumber}`;
  rawProviderReturns.push({
    provider_return_id: providerReturnId,
    synthetic_replicate_id: job.synthetic_replicate_id,
    reviewer_dispatch_id: job.dispatch_id,
    stage,
    ...(stage === "reaction" ? { position_seen: position + 1 } : {}),
    attempt_number: attemptNumber,
    accepted: errors.length === 0,
    validation_errors: [...errors],
    raw_return: cloneJson(result),
  });
  if (errors.length > 0) {
    rejectedAttempts.push({
      provider_return_id: providerReturnId,
      synthetic_replicate_id: job.synthetic_replicate_id,
      reviewer_dispatch_id: job.dispatch_id,
      stage,
      ...(stage === "reaction" ? { position_seen: position + 1 } : {}),
      attempt_number: attemptNumber,
      validation_errors: [...errors],
      disposition: attemptNumber === 1 ? "retried" : "retry_exhausted",
    });
  }
  return providerReturnId;
};

const normalizeReaction = (item, result, providerReturnId) => deepFreeze({
  ...cloneJson(result),
  reaction_id: `${item.job.response_id}-reaction-${item.position + 1}`,
  variation_id: item.job.shown_order[item.position],
  display_label_seen: item.job.blind_labels[item.job.shown_order[item.position]],
  position_seen: item.position + 1,
  source_provenance: choiceSource(providerReturnId),
});

const reactionItems = jobs.flatMap(job => job.reaction_prompts.map((prompt, position) => ({
  job,
  position,
  prompt,
})));

const reactionReturns = await pipeline(reactionItems, item => agent(item.prompt, {
  label: `${item.job.dispatch_id}-reaction-${item.position + 1}`,
  schema: args.reaction_schema,
}));

const validatedReactionReturns = new Map();
const reactionRetryItems = [];
for (let index = 0; index < reactionItems.length; index += 1) {
  const item = reactionItems[index];
  const result = reactionReturns[index];
  const errors = reactionErrors(item.job, item.position, result);
  const providerReturnId = recordProviderReturn(item, "reaction", 1, result, errors);
  if (errors.length === 0) {
    validatedReactionReturns.set(
      reactionKey(item.job, item.position),
      normalizeReaction(item, result, providerReturnId),
    );
  } else {
    reactionRetryItems.push({ ...item, errors });
    validationFailures.set(reactionKey(item.job, item.position), errors);
  }
}

if (reactionRetryItems.length > 0) {
  const reactionRetryReturns = await pipeline(reactionRetryItems, item => agent(
    `${item.prompt}\n\nYour prior reaction return failed validation:\n- ${item.errors.join("\n- ")}\nReturn only the corrected reaction object. No additional creative is revealed.`,
    {
      label: `${item.job.dispatch_id}-reaction-${item.position + 1}-retry`,
      schema: args.reaction_schema,
    },
  ));
  for (let index = 0; index < reactionRetryItems.length; index += 1) {
    const item = reactionRetryItems[index];
    const result = reactionRetryReturns[index];
    const errors = reactionErrors(item.job, item.position, result);
    const providerReturnId = recordProviderReturn(item, "reaction", 2, result, errors);
    if (errors.length === 0) {
      validatedReactionReturns.set(
        reactionKey(item.job, item.position),
        normalizeReaction(item, result, providerReturnId),
      );
      validationFailures.delete(reactionKey(item.job, item.position));
    } else {
      validationFailures.set(reactionKey(item.job, item.position), errors);
    }
  }
}

const buildComparisonItems = (candidateJobs, frozenReturns) => candidateJobs
  .filter(job => job.shown_order.every(
    (_variationId, position) => frozenReturns.has(reactionKey(job, position)),
  ))
  .map(job => {
    const reactions = deepFreeze(job.shown_order.map(
      (_variationId, position) => frozenReturns.get(reactionKey(job, position)),
    ));
    const frozenForPrompt = reactions.map(reaction => ({
      reaction_id: reaction.reaction_id,
      variation_id: reaction.variation_id,
      display_label_seen: reaction.display_label_seen,
      position_seen: reaction.position_seen,
      reaction_label: reaction.reaction_label,
      immediate_reaction: reaction.immediate_reaction,
      noticed_or_understood_first: reaction.noticed_or_understood_first,
      strongest_positive_signal: reaction.strongest_positive_signal,
      strongest_negative_signal: reaction.strongest_negative_signal,
      judgment_status: reaction.judgment_status,
    }));
    return {
      job,
      position: null,
      reactions,
      prompt: `${job.comparison_prompt}\n\nFROZEN VALIDATED REACTIONS (preserve IDs and text exactly):\n${JSON.stringify(frozenForPrompt)}`,
    };
  });

// No comparison prompt exists until every assigned reaction is validated and frozen.
const comparisonItems = buildComparisonItems(jobs, validatedReactionReturns);
const comparisonReturns = await pipeline(comparisonItems, item => agent(item.prompt, {
  label: `${item.job.dispatch_id}-comparison`,
  schema: args.comparison_schema,
}));

const acceptedComparisons = new Map();
const comparisonRetryItems = [];
for (let index = 0; index < comparisonItems.length; index += 1) {
  const item = comparisonItems[index];
  const result = comparisonReturns[index];
  const errors = comparisonErrors(item.job, item.reactions, result);
  const providerReturnId = recordProviderReturn(item, "comparison", 1, result, errors);
  if (errors.length === 0) {
    acceptedComparisons.set(item.job.dispatch_id, {
      result: deepFreeze(cloneJson(result)),
      providerReturnId,
      reactions: item.reactions,
    });
  } else {
    comparisonRetryItems.push({ ...item, errors });
    validationFailures.set(`${item.job.dispatch_id}:comparison`, errors);
  }
}

if (comparisonRetryItems.length > 0) {
  const comparisonRetryReturns = await pipeline(comparisonRetryItems, item => agent(
    `${item.prompt}\n\nYour prior comparison return failed validation:\n- ${item.errors.join("\n- ")}\nReturn only the corrected comparison object. The reactions above remain frozen.`,
    {
      label: `${item.job.dispatch_id}-comparison-retry`,
      schema: args.comparison_schema,
    },
  ));
  for (let index = 0; index < comparisonRetryItems.length; index += 1) {
    const item = comparisonRetryItems[index];
    const result = comparisonRetryReturns[index];
    const errors = comparisonErrors(item.job, item.reactions, result);
    const providerReturnId = recordProviderReturn(item, "comparison", 2, result, errors);
    if (errors.length === 0) {
      acceptedComparisons.set(item.job.dispatch_id, {
        result: deepFreeze(cloneJson(result)),
        providerReturnId,
        reactions: item.reactions,
      });
      validationFailures.delete(`${item.job.dispatch_id}:comparison`);
    } else {
      validationFailures.set(`${item.job.dispatch_id}:comparison`, errors);
    }
  }
}

const runtimeAttemptsFor = job => rawProviderReturns
  .filter(item => item.reviewer_dispatch_id === job.dispatch_id)
  .map(item => ({
    attempt_id: item.provider_return_id,
    stage: item.stage,
    ...(item.stage === "reaction" ? { position_seen: item.position_seen } : {}),
    attempt_number: item.attempt_number,
    provider_return_id: item.provider_return_id,
    outcome: item.accepted ? "accepted" : "rejected",
    validation_errors: [...item.validation_errors],
  }));

const buildResponse = (job, accepted) => {
  const response = {
    study_id: job.study_id,
    response_id: job.response_id,
    record_type: job.record_type,
    method: job.method,
    synthetic_replicate_id: job.synthetic_replicate_id,
    reviewer_dispatch_id: job.dispatch_id,
    persona_archetype_id: job.persona_archetype_id,
    segment_id: job.segment_id,
    ...(job.context_stratum_id ? { context_stratum_id: job.context_stratum_id } : {}),
    ...(
      "audience_slot_id" in job
        ? {
          audience_slot_id: job.audience_slot_id,
          grounded_profile_id: job.grounded_profile_id,
          profile_snapshot_sha256: job.profile_snapshot_sha256,
        }
        : {}
    ),
    profile_snapshot: cloneJson(job.profile_snapshot),
    context_attribute_provenance: cloneJson(job.context_attribute_provenance),
    worker_context_isolation: job.worker_context_isolation,
    human_sample_independence: false,
    assigned_variation_ids: [...job.variation_ids],
    blind_labels: cloneJson(job.blind_labels),
    shown_order: [...job.shown_order],
    reaction_protocol: job.reaction_protocol,
    runtime_attempts: runtimeAttemptsFor(job),
    validation: {
      schema_valid: true,
      assignment_valid: true,
      reaction_order_valid: true,
    },
  };
  const frozenReactionIds = accepted.reactions.map(reaction => reaction.reaction_id);
  const comparisonSource = choiceSource(accepted.providerReturnId);
  if (job.record_type === "screening_response" && job.method === "partial_exposure_maxdiff") {
    return {
      ...response,
      per_creative_reactions: cloneJson(accepted.reactions),
      comparative_choice: {
        ...cloneJson(accepted.result.comparative_choice),
        frozen_reaction_ids: frozenReactionIds,
        source_provenance: comparisonSource,
      },
      usable_maxdiff_block: accepted.result.usable_maxdiff_block,
    };
  }
  if (job.record_type === "screening_response" && job.method === "complete_exposure") {
    return {
      ...response,
      per_creative_reactions: cloneJson(accepted.reactions),
      complete_set_evaluation: {
        ...cloneJson(accepted.result.complete_set_evaluation),
        frozen_reaction_ids: frozenReactionIds,
        source_provenance: comparisonSource,
      },
      usable_complete_exposure_observation: accepted.result.usable_complete_exposure_observation,
    };
  }
  if (job.record_type === "boundary_response") {
    return {
      ...response,
      per_creative_reactions: cloneJson(accepted.reactions),
      pairwise_choice: {
        ...cloneJson(accepted.result.pairwise_choice),
        frozen_reaction_ids: frozenReactionIds,
        source_provenance: comparisonSource,
      },
      usable_pairwise_observation: accepted.result.usable_pairwise_observation,
    };
  }
  const assessments = new Map(
    accepted.result.finalist_assessments.map(item => [item.variation_id, item]),
  );
  return {
    ...response,
    finalist_reviews: accepted.reactions.map(reaction => ({
      ...cloneJson(reaction),
      rubric_scores: cloneJson(assessments.get(reaction.variation_id).rubric_scores),
      feedback: cloneJson(assessments.get(reaction.variation_id).feedback),
      rubric_source_provenance: comparisonSource,
    })),
    final_preference_ranking: [...accepted.result.final_preference_ranking],
  };
};

const responses = jobs
  .filter(job => acceptedComparisons.has(job.dispatch_id))
  .map(job => buildResponse(job, acceptedComparisons.get(job.dispatch_id)));
const completedReplicateIds = new Set(
  responses.map(response => response.synthetic_replicate_id),
);
const missingSyntheticReplicateIds = jobs
  .filter(job => !completedReplicateIds.has(job.synthetic_replicate_id))
  .map(job => job.synthetic_replicate_id);

return {
  run_id: args.run_id || null,
  status: missingSyntheticReplicateIds.length === 0 ? "complete" : "incomplete",
  requested_replicates: jobs.length,
  completed_replicates: responses.length,
  missing_synthetic_replicate_ids: missingSyntheticReplicateIds,
  responses,
  raw_provider_returns: rawProviderReturns,
  rejected_attempts: rejectedAttempts,
  validation_failures: Object.fromEntries(validationFailures),
  dispatch_audit: jobs.map(job => ({
    record_type: job.record_type,
    synthetic_replicate_id: job.synthetic_replicate_id,
    reviewer_dispatch_id: job.dispatch_id,
    accepted: completedReplicateIds.has(job.synthetic_replicate_id),
    attempt_contract: {
      retry_limit_per_return: 1,
      reaction_positions: job.shown_order.map((_variationId, position) => position + 1),
      comparison_required: true,
    },
    reaction_attempts: job.shown_order.map((_variationId, position) => (
      rawProviderReturns.filter(item => (
        item.reviewer_dispatch_id === job.dispatch_id
        && item.stage === "reaction"
        && item.position_seen === position + 1
      )).length
    )),
    comparison_attempts: rawProviderReturns.filter(item => (
      item.reviewer_dispatch_id === job.dispatch_id && item.stage === "comparison"
    )).length,
  })),
};
