import { describe, expect, it } from "bun:test";
import { mapEndpoint, mapJob, mapModel } from "../src/adapters/mappers";

describe("mapJob CLI snake_case", () => {
  it("reads job_id and job_type from r4 job list --json", () => {
    const job = mapJob({
      job_id: "lkmo7cua-real",
      job_type: "endpoint_provision",
      status: "running",
    });
    expect(job.id).toBe("lkmo7cua-real");
    expect(job.type).toBe("endpoint_provision");
    expect(job.status).toBe("running");
  });

  it("falls back to id/type when snake_case missing", () => {
    const job = mapJob({ id: "legacy", type: "batch", status: "queued" });
    expect(job.id).toBe("legacy");
    expect(job.type).toBe("batch");
  });
});

describe("mapModel CLI snake_case", () => {
  it("reads model_id from r4 model list --json", () => {
    const model = mapModel({
      model_id: "qwen3.6-35b@rikyu",
      name: "qwen3.6-35b@rikyu",
      scope: "user",
    });
    expect(model.id).toBe("qwen3.6-35b@rikyu");
    expect(model.name).toBe("qwen3.6-35b@rikyu");
  });
});

describe("mapEndpoint cold-start (T5-topic-5, capability-detected)", () => {
  const base = {
    instance_id: "ep-abc",
    model: "rivault-gpt-oss-static",
    status: "ready",
    host: "109.0.1.42",
    port: 8000,
    tier: "project",
    backend: "slurm",
  };

  it("derives coldStartMs when created_at + ready_at both parse", () => {
    const ep = mapEndpoint({
      ...base,
      created_at: "2026-09-18T00:00:00Z",
      ready_at: "2026-09-18T00:01:12Z",
    });
    expect(ep.coldStartMs).toBe(72_000);
  });

  it("returns undefined (no fabricated chip) when either stamp is missing", () => {
    expect(mapEndpoint({ ...base, created_at: "2026-09-18T00:00:00Z" }).coldStartMs).toBeUndefined();
    expect(mapEndpoint({ ...base, ready_at: "2026-09-18T00:01:12Z" }).coldStartMs).toBeUndefined();
    expect(mapEndpoint({ ...base }).coldStartMs).toBeUndefined();
  });

  it("returns undefined when ready_at precedes created_at (corrupt stamps, never negative chip)", () => {
    expect(
      mapEndpoint({
        ...base,
        created_at: "2026-09-18T00:02:00Z",
        ready_at: "2026-09-18T00:01:00Z",
      }).coldStartMs,
    ).toBeUndefined();
  });

  it("returns undefined when either stamp is unparseable", () => {
    expect(
      mapEndpoint({
        ...base,
        created_at: "not-a-date",
        ready_at: "2026-09-18T00:01:12Z",
      }).coldStartMs,
    ).toBeUndefined();
  });
});
