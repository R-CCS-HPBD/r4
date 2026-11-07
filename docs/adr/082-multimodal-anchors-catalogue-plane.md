# ADR-082: Multimodal anchors in the catalogue plane — facets, regions, media fragments, header probes, excerpts

**Status:** Proposed (2026-10-03)
**Date:** 2026-10-03
**Relates to:** ADR-031 (amended: `node_id` is the catalogue → spine anchor; regions are catalogue sub-objects), ADR-081 (typed spine), ADR-084 (results adapters), ADR-061 (sandbox profiles), ADR-077 (bound storage), ADR-080 (access guard), CONSTITUTION §2 (no presigned URLs to clients), CONSTITUTION §2.1 (sandbox rule), gap rows D-12 (prerequisite: the `embedding-job` sandbox leg), D-14, D-15, D-18, D-38, **D-60**

## Context

Background, method and report: [ADR-081](081-typed-knowledge-spine.md) and
kg-multimodal-investigation-2026-10-03.md.
The three test corpora are media-heavy: example-project-b (video clips, detection
tables, evidence frames, weights), example-project-a (recordings that live only on
another site, plus inventories and metrics), example-project-c (large CT slices,
model outputs). Verified at umrs `d28978ad`:

- **Media enter the catalogue without content.** Video ingest writes the
  literal summary `video file <name>` (`data_catalog/extract.py:48`);
  perception has no video branch (`data_catalog/perception.py:110-128`) and
  captions images only when enabled (default false, `perception.py:80`).
  The 1 MiB extract sample (`data_catalog/ingest.py:427-434`) loses even the
  size of a large slice — MM-06, MM-07, ING-04. Live rccs: every video row
  reads `video file …`; perception skipped for most objects (LIVE-16).
- **No temporal or spatial addressing exists.** Edges carry only
  source/target/relation/content (`migrations/0055_knowledge_store.sql:102-110`);
  the evidence request has `claim_id`, `content`, `content_uri` and no
  segment (`api/evidence.py:44-53`); segment tables exist only in
  `demos/video-feed/schema/001_video_feed.sql:50-72` — MM-04.
- **Catalogue and KG meet by heuristic.** The link is a read-time match on
  `s3_object` or filename (`data_catalog/links.py:24-41`) — KG-10. One
  `.mp4` gets a different modality in at least eight schemes (`movie`,
  `video`, `document`, `result`, `text` …) — MM-10. "Stable" catalogue ids
  use Python's salted `hash()` (`data_catalog/artifacts.py:38`) — MM-12.
- **Results never arrive.** Byte uploads register with `content_hash ''`
  and UNDERSTAND skips them (`jobs/knowledge_adapters.py:789-800, 1154`) —
  ING-01. Workflow outputs use `s3://bucket/key` verbatim as the key and are
  parsed as Python (`knowledge_adapters.py:1474-1481`) — ING-02; live, an
  example-project-b run (many output keys) ended in "understand: all 2
  artifact(s) failed, no IR produced" — MM-02. Vision results are never
  registered (`api/vision.py:302-308`) — MM-01.
- **Clocks disagree and are not recorded** (DATA-05): example-project-b clips are
  VFR (nominal and average frame rates differ); the production TSVs
  run on a nominal-fps axis (not real time); example-project-b file times are
  naive local; example-project-a file names are local time while metadata is UTC; clip offsets
  live only in file names. `VideoStep` computes frame time as
  `frame_idx/fps` with no PTS (`pipeline_sdk/templates/video_step.py:150-193`).
- **The agent cannot look.** `lab_file_read` returns `{binary: true}` for
  media (`r4/src/r4/search/catalog_format.py:114`) — MM-05; and CONSTITUTION
  §2 forbids handing it a presigned URL.

## Decision

**D-1 — Catalogue facets (owner `umrs.data_catalog`).** `catalog_objects`
gains `content_sha256` + `checksum_basis` (`sha256 |
sha256_head_tail_1MiB | none`), `size_bytes`, `mime_type`, `storage_ref`
(`central | bound:<project> | external:<site>`), `asset_class` (FK term,
ADR-081: the one modality vocabulary; `detected_modality` stays as a legacy
facet), `duration_s`, `n_frames`, `fps_nominal`, `fps_avg`, `time_base`,
`width`, `height`, `depth`, `pixel_size/unit`, `time_origin` (NULL when the
time zone is unknown — never guessed) with `time_origin_local` and
`time_origin_src`, `derived_from_key` + `offset_s` for clips, `facets` JSONB
(codec, audio none/silent/present, mean dB, illumination, EXIF, bit depth,
weights licence …) with `facets_extractor`, and `node_id` (FK, ON DELETE SET
NULL) — the downward anchor to the asset node. `content_sha256` closes
D-18's "URI + checksum on every S3 row".

**D-2 — Regions are catalogue sub-objects.** `catalog_regions` holds
addressing only: deterministic `region_id`; `(project_id, s3_key)` FK; kind
`interval | frame | bbox | interval_bbox | polygon | mask | slice_range |
tile | voxel_box | rows | text_span`; `t_range` (PTS seconds on that
object), `time_basis` + `raw_t`, `frame_range`, `wall_range` (tstzrange,
NULL when the zone is unknown), `bbox`, `polygon`, `mask_key`, `z_range`,
`row_range` (RFC 7111, 1-based data rows), `char_range` (RFC 5147),
`coord_frame` + `frame_transform`; a canonical `fragment` string (W3C Media
Fragments `#t=` / `#xywh=`, `#row=`, `#char=`); `node_id` (the node this
region evidences) and `role` (evidence, subject, keyframe, support,
context). `attrs` holds only the producer's measurement for that region;
the semantic authority stays on the node. GiST indexes on `(s3_key,
t_range)`, `(project_id, wall_range)`, `(s3_key, z_range)`.

**D-3 — The time-base rule.** `t_range` is PTS seconds on its object.
Each producer's `time_basis` (`pts | nominal_fps | avg_fps | declared`) is
declared in the project profile after reading that producer's code; when
it is not `pts`, `raw_t` keeps the original and the conversion is flagged
approximate unless a PTS sidecar exists. `wall_range` exists only when the
zone or UTC is known. A region on a derived clip is mirrored onto its
source at `t + offset_s`, so ground truth in source time and detections in
clip time meet in one index. A region ending past its object's duration is
flagged, never clipped.

**D-4 — The cardinality rule.** Nodes are what agents reason about:
assets, events, keyframes, runs, models, parameter sets, ground truth,
metrics, claims, slices and tiles (fixed-size tiles of a large slice).
Per-frame detections and patch tokens stay inside their table asset and are
cited by `#row=` regions. Target: 10³–10⁵ nodes per project.

**D-5 — `MEDIA_PROBE`, header-only, sandboxed.** Extractors run in the
ADR-061 `embedding-job` profile (`--network=none`): ffprobe streams/format,
one sampled frame for illumination and a keyframe, volumedetect over 30 s
when audio exists; PIL headers with `getexif` in its own `try` (the MM-07
fix); h5py/zarr/npy headers and NXtomo paths; CSV header profiles; flattened
JSON scalar leaves; log head/tail/error lines; weights sha256, manifest and
licence; instrument sidecar JSON. Media bytes never reach an LLM. Missing
acquisition metadata (pixel size, instrument, camera model) surfaces as
ADR-081 `missing_required`, never invented. Prerequisite: these parsers
read untrusted uploaded media, and CONSTITUTION §2.1 records that
embedding/ingest compute still runs outside a sandbox (gap D-12, Partial)
and forbids adding new such workloads. `MEDIA_PROBE` and `media_excerpt`
(D-6) therefore land only after D-12's `embedding-job` sandbox leg is live;
they never run in the unsandboxed ingest path.

**D-6 — `media_excerpt` instead of URLs.** Evidence leaves the platform as
keys plus fragments. When a person or agent asks to see a region, a
sandboxed job cuts a small JPG or a clip of at most 20 s keyed by
`region_id`, stores it under the project's derived prefix, and the client
fetches it through the gateway files route. No presigned URL and no
credential reaches a client (CONSTITUTION §2, D-16/D-37).

**D-7 — ADR-031 amendment.** Invariant text gains: (a) `catalog_objects.node_id`
is the only catalogue → spine reference (it replaces the filename heuristic
in `data_catalog/links.py`); (b) regions are catalogue sub-objects that
point down to the node they evidence; (c) vector rows hang off `node_id`
only and never reference catalogue or region rows (keyframes and tiles are
nodes). Catalogue rows and regions inherit the parent graph's visibility
predicate and ADR-080 access.

**D-8 — Results land as anchors.** The ADR-084 results job reads each
step's `output_keys` through the ADR-077 binding (not `s3_uri` descriptors)
and writes a catalogue row + asset node per output (ING-01, ING-02, MM-02).
Vision results become asset nodes, `r4:Segmentation` nodes and bbox regions
with the prompt persisted (MM-01, MM-15).

### Why

- ADR-031 holds: the catalogue owns what physically exists, the spine owns
  meaning, references flow down. The two other panel designs duplicated
  catalogue facts in spine-side asset tables and handed out presigned URLs.
- Existing standards (Media Fragments, RFC 7111, RFC 5147) map one-to-one
  to `oa:FragmentSelector` in the RDF export (ADR-032 addendum).
- The corpora have three clocks; a declared basis plus `raw_t` makes every
  conversion auditable instead of silently wrong.

## Consequences

- Positive: an agent fetches a few-second clip instead of the full
  video; questions of the form "where in which video, slice or table row"
  have exact answers; facet discovery (`r4 data assets --class
  r4:VideoAsset --where illumination=colour audio=present`) shows what
  exists — the prototype found colour clips and clips with audio in a corpus
  whose reports say only IR footage was evaluated (DATA-07).
- Cost: one probe per object (ffprobe plus one decoded frame); region volume
  (tens of thousands of regions per project in the prototype); per-producer time-base
  declarations are manual work. Objects on other sites (e.g. recordings
  held on another site) get `storage_ref external:<site>` rows with facets
  from inventories only, and no excerpt.
- Residency: catalogue and region rows of ADR-077 bound projects stay in
  `umrs_db` until leg 2 lands — a maintainer decision.
- NOT in scope: captions as facts (ADR-084: proposed notes only); image
  and video embedding spaces (ADR-083); the agent verbs that read regions
  (ADR-085); an r4-web player with `#t=` overlays (follow-up).

## Alternatives Considered

- **Observation Spine** — `kg_assets` keyed upward into `catalog_objects`
  (breaks ADR-031) and evidence as presigned URLs (breaks CONSTITUTION §2).
  Grafted: wall-clock ranges, clock source, `frame_transform`, text-quote
  regions.
- **Verb-First** — a spine-side `knowledge_assets` table duplicating
  catalogue facts and presigned thumbnails. Grafted: the producer-declared
  `time_basis`, fixed-size tiling of large slices.
- **Do nothing** — on media/derived questions Haiku scored 0.43 with files
  only and 0.39 with today's tools (worse), 0.57 with these anchors; Opus
  0.96 / 0.96 / 1.00.
- **One node per region or per frame** — a prototype loader did
  this for detections (per-frame detection nodes, most of the
  graph); it stayed fast for typed reads, but the trigram search lane's
  worst case (about 0.9 s on tokens every detection carries) came from
  these rows. Production follows D-4.

## Acceptance receipts

1. One live project run on rccs: every output has a catalogue row with
   `content_sha256` and an asset node; its events carry `#t=` regions on
   the source video with the producer's declared basis.
2. A clip region is mirrored onto its source; `media_excerpt` returns a
   ≤ 20 s clip through the files route; no response contains a presigned URL.
3. A large slice (beyond the 1 MiB sample) keeps its width and height (MM-07 regression test).
4. Recordings held on another site appear as `external:<site>` rows; a request for an
   excerpt says "not reachable from this site".
5. `MEDIA_PROBE` and `media_excerpt` run in the `embedding-job` profile:
   a probe job's container shows `--network=none` and an outbound
   connection attempt from it fails (D-12 leg verified first).

## Evidence (2026-10-03)

- Verifier (investigation verifier report):
  all prototype regions join a catalogue object; time order valid;
  every bbox inside its frame and equal to its `#xywh`; every `#row`/`#char`
  range inside its file; every out-of-duration range flagged (data issues,
  e.g. a ground-truth event ending past its video's duration). A frame cut at
  a known offset of a source video shows the expected burned-in clock time, which
  confirms the PTS axis.
- Video corpus facets: all clips VFR (nominal vs average rate differ); audio
  present on some, silent on most; illumination mostly IR, some colour. Both video producers
  compute seconds as `POS_FRAMES / CAP_PROP_FPS`, and OpenCV returns the
  average rate, so their basis is `avg_fps`; earlier motion events on the
  nominal axis were converted with `raw_t` kept.
- Benchmark (ADR-085 has the arms): the measured gain is on structured and
  media questions, not prose (Haiku media/derived 0.43 / 0.39 / 0.57,
  cross-modal 0.30 / 0.20 / 0.50, prose-only 0.69 / 0.62 / 0.69). With the
  KG, Haiku's ffprobe calls fell 7 → 1 and Opus's ffprobe/ffmpeg calls
  26 → 14: agents stopped re-probing media.

## Rollout-status (2026-10-03)

Proposed; nothing landed (see ADR-081 for what the investigation did and
did not touch; wave ids are the report's §7.3). Gates wave W3 (migrations
0121–0122, provisional numbers), together with ADR-084 D-5 (result
adapters); `MEDIA_PROBE` and `media_excerpt` additionally wait for D-12's
`embedding-job` sandbox leg. Open: ADR-077 leg-2 residency; the excerpt
cache lifetime and quota.

## Related

- [ADR-031](031-knowledge-layer-plane-ownership.md), [ADR-061](061-mini-rivault-sandbox-profiles.md), [ADR-077](077-per-project-external-storage-binding.md), [ADR-080](080-one-access-guard-shareable-resources.md)
- [ADR-081](081-typed-knowledge-spine.md), [ADR-083](083-embedding-spaces.md), [ADR-084](084-growth-governance.md), [ADR-085](085-agent-kg-verb-contract.md)
- docs/development/kg-multimodal-investigation-2026-10-03.md

*(2026-11-06: partner-demo specifics replaced with neutral wording; the originals are kept in the private r4-private-demo repository (repo-rename plan P5b-3).)*
