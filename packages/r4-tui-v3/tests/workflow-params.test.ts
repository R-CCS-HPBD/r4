/**
 * Run parameters a registered workflow package reveals (2026-10-02) — the
 * card's "how to run" and "parameters" sections. Mirrors the UMRS runner:
 * a run's parameters fill every step's config as defaults, so a key the
 * step's own config sets is not a parameter for that step; reserved keys
 * never are; project_id / input_files come from the run itself.
 */
import { describe, expect, test } from "bun:test";
import {
  describeParam,
  parsePyDefault,
  triggerCommand,
  workflowRunSpec,
  type WorkflowParam,
} from "../src/lib/workflow-params";
import type { WorkflowDefinitionDetail } from "../src/stores/umrs-workflow";

function pkg(partial: Partial<WorkflowDefinitionDetail>): WorkflowDefinitionDetail {
  return { workflow_name: "wf", ...partial };
}

function file(path: string, content: string) {
  return { path, content, bytes: content.length, lines: content.split("\n").length, truncated: false };
}

const byName = (params: WorkflowParam[]) => Object.fromEntries(params.map((p) => [p.name, p]));

describe("workflowRunSpec", () => {
  test("schema parameters carry type, default, description and choices; required first", () => {
    const { params } = workflowRunSpec(
      pkg({
        parameter_schema: {
          properties: {
            mode: { type: "string", enum: ["fast", "full"], default: "fast", title: "run mode" },
            sample: { type: "string", description: "sample id" },
          },
          required: ["sample"],
        },
      }),
    );
    expect(params.map((p) => p.name)).toEqual(["sample", "mode"]);
    expect(params[0]).toMatchObject({ required: true, type: "string", description: "sample id", sources: ["schema"] });
    expect(params[1]).toMatchObject({ required: false, default: "fast", choices: ["fast", "full"], description: "run mode" });
  });

  test("{{ params.x }} in a step config needs a value unless that step's config sets x", () => {
    const spec = workflowRunSpec(
      pkg({
        steps: [
          {
            name: "ask",
            config: {
              temperature: 0,
              prompt_template: "Q: {{ params.question }} T={{ params.temperature }} P={{ params.project_id }} {{ steps.x.y }}",
            },
          },
        ],
      }),
    );
    const params = byName(spec.params);
    expect(Object.keys(params)).toEqual(["question"]);
    expect(params.question).toMatchObject({ required: true, sources: ["template"], usedBy: ["ask"] });
    expect(spec.readsProject).toBe(true); // filled from the active project, not listed
  });

  test("code reads: own-config keys skipped, defaults parsed, config[k] required, reserved keys never", () => {
    const code = [
      "class Seg(Step):",
      "    def setup(self, config):",
      "        self.threshold = config.get('threshold', 0.5)",
      "        self.model = config.get(\"model_type\", \"sam3\")",
      "        self.shape = config.get('shape', (1, 2))",
      "        self.bucket = config['bucket']",
      "        self.key = config.get('api_key')",
      "        self.name = config.get('step_name')",
      "        self.raw = config.get('_workflow_params', {})",
      "        self.files = config.get('input_files', [])",
    ].join("\n");
    const spec = workflowRunSpec(
      pkg({
        steps: [{ name: "segment", entrypoint: "seg:Seg", config: { threshold: 0.7 } }],
        files: [file("steps/seg.py", code)],
      }),
    );
    const params = byName(spec.params);
    expect(Object.keys(params).sort()).toEqual(["bucket", "model_type", "shape"]);
    expect(params.bucket).toMatchObject({ required: true, usedBy: ["segment"], sources: ["code"] });
    expect(params.model_type).toMatchObject({ required: false, default: "sam3" });
    expect(params.shape!.default).toBe("(1, 2)"); // an expression, shown verbatim
    expect(spec.readsInputFiles).toBe(true);
  });

  test("a helper module counts only for keys no step config sets", () => {
    const spec = workflowRunSpec(
      pkg({
        steps: [{ name: "a", entrypoint: "a:A", config: { poll: 5 } }],
        files: [file("lib/util.py", "x = config.get('poll', 2)\ny = config.get('retries', 3)\n")],
      }),
    );
    expect(spec.params.map((p) => [p.name, p.usedBy])).toEqual([["retries", ["lib/util.py"]]]);
  });

  test("a root vision step needs its image; a downstream one falls back to its parent", () => {
    const vision = "umrs.pipeline_sdk.templates.vision_step:VisionStep";
    const root = byName(workflowRunSpec(pkg({ steps: [{ name: "see", entrypoint: vision, config: { image_param: "figure" } }] })).params);
    expect(root.figure).toMatchObject({ required: true, sources: ["builtin"], usedBy: ["see"] });
    const later = byName(
      workflowRunSpec(pkg({ steps: [{ name: "src" }, { name: "see", entrypoint: vision, depends_on: ["src"] }] })).params,
    );
    expect(later.image).toMatchObject({ required: false });
  });
});

describe("trigger line and rows", () => {
  const params: WorkflowParam[] = [
    { name: "bucket", required: true, sources: ["schema"], usedBy: [] },
    { name: "ids", type: "array", required: true, sources: ["schema"], usedBy: [] },
    { name: "mode", required: false, default: "fast", sources: ["schema"], usedBy: [] },
  ];

  test("required text values become --param k=, lists go in --params-json, defaults stay out", () => {
    expect(triggerCommand("wf", params)).toBe(`/workflow trigger wf --param bucket= --params-json '{"ids": []}'`);
    expect(triggerCommand("wf", [])).toBe("/workflow trigger wf");
  });

  test("describeParam: marker, type, default, description, choices, inferred origin", () => {
    expect(describeParam({ name: "mode", type: "string", required: false, default: "two words", choices: ["a", "b"], description: "how", sources: ["schema"], usedBy: [] })).toBe(
      'mode  string  = "two words"  — how  (one of a|b)',
    );
    expect(describeParam({ name: "question", required: true, sources: ["template"], usedBy: ["ask"] })).toBe(
      "question*  [prompt · ask]",
    );
  });

  test("parsePyDefault", () => {
    expect([parsePyDefault("None"), parsePyDefault("True"), parsePyDefault("3.5"), parsePyDefault("'x'"), parsePyDefault("CONST")]).toEqual([
      null, true, 3.5, "x", "CONST",
    ]);
  });
});
