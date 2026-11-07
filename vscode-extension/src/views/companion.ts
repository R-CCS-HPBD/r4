/**
 * Research Companion sidebar tree view.
 *
 * Provides quick access to companion capabilities from the VS Code
 * sidebar. Each capability launches a research-focused chat with the
 * appropriate context through Gateway LLM APIs.
 *
 * Architecture constraint: this is a thin UI shell. The companion
 * agent loop, harness, and context management run in the Python r4
 * process. The extension uses Gateway /gw/llm/* for LLM requests
 * and will integrate with /gw/companion/* when those routes ship.
 */

import * as vscode from "vscode";
import type { GatewayClient } from "../gateway-client";

// ── Companion capability definitions ─────────────────────────

export interface CompanionCapability {
  readonly id: string;
  readonly label: string;
  readonly description: string;
  readonly icon: string;
  readonly systemPrompt: string;
}

const CAPABILITIES: CompanionCapability[] = [
  {
    id: "literature",
    label: "Literature Search",
    description: "Search and summarize research papers",
    icon: "book",
    systemPrompt:
      "You are a research companion specializing in scientific literature " +
      "review. Help the researcher find, summarize, and synthesize relevant " +
      "papers. When discussing papers, always include: title, authors, year, " +
      "key findings, methodology, and relevance to the research question. " +
      "Suggest follow-up searches and related work.",
  },
  {
    id: "hypothesis",
    label: "Hypothesis Generation",
    description: "Formulate and refine research hypotheses",
    icon: "lightbulb",
    systemPrompt:
      "You are a research companion specializing in hypothesis generation " +
      "and experimental design. Help the researcher formulate testable " +
      "hypotheses based on their observations, prior work, and domain " +
      "knowledge. For each hypothesis, suggest: null/alternative formulations, " +
      "key variables, potential confounds, and feasible test approaches.",
  },
  {
    id: "experiment",
    label: "Experiment Tracking",
    description: "Design and track computational experiments",
    icon: "beaker",
    systemPrompt:
      "You are a research companion specializing in computational experiment " +
      "design and tracking for HPC environments. Help the researcher plan " +
      "experiments: define parameters, resource requirements (GPUs, memory, " +
      "wall time), expected outputs, success criteria, and reproducibility " +
      "checklists. Track experiment status and suggest next steps based on " +
      "results.",
  },
  {
    id: "analysis",
    label: "Data Analysis",
    description: "Analyze results and suggest visualizations",
    icon: "graph",
    systemPrompt:
      "You are a research companion specializing in scientific data analysis. " +
      "Help the researcher analyze experimental results, identify patterns, " +
      "suggest appropriate statistical tests, and recommend visualizations. " +
      "When reviewing data, consider: sample size adequacy, distribution " +
      "assumptions, effect sizes, confidence intervals, and reproducibility.",
  },
  {
    id: "code",
    label: "Research Code Review",
    description: "Review and improve research code",
    icon: "code",
    systemPrompt:
      "You are a research companion specializing in scientific computing " +
      "code review. Help the researcher improve code quality, performance, " +
      "and reproducibility. Focus on: numerical stability, parallelization " +
      "opportunities (MPI, GPU), memory efficiency for large datasets, " +
      "proper random seed handling, result serialization, and HPC job " +
      "script best practices (Slurm, Singularity/Apptainer).",
  },
];

// ── Tree items ───────────────────────────────────────────────

export class CompanionCapabilityItem extends vscode.TreeItem {
  constructor(public readonly capability: CompanionCapability) {
    super(capability.label, vscode.TreeItemCollapsibleState.None);
    this.description = capability.description;
    this.tooltip = capability.description;
    this.iconPath = new vscode.ThemeIcon(capability.icon);
    this.contextValue = "companionCapability";
    this.command = {
      command: "r4.companionChat",
      title: `Start ${capability.label}`,
      arguments: [capability],
    };
  }
}

class CompanionHeaderItem extends vscode.TreeItem {
  constructor(connected: boolean) {
    super(
      connected ? "Research Companion" : "Not Connected",
      vscode.TreeItemCollapsibleState.Expanded,
    );
    this.iconPath = new vscode.ThemeIcon(
      connected ? "mortar-board" : "debug-disconnect",
    );
    this.contextValue = "companionHeader";
    this.description = connected
      ? "AI-powered research assistant"
      : "Login to Gateway first";
  }
}

// ── Tree provider ────────────────────────────────────────────

export class CompanionTreeProvider
  implements vscode.TreeDataProvider<vscode.TreeItem>, vscode.Disposable
{
  private readonly _onDidChange =
    new vscode.EventEmitter<vscode.TreeItem | undefined>();
  readonly onDidChangeTreeData = this._onDidChange.event;

  constructor(private readonly getClient: () => GatewayClient | undefined) {}

  refresh(): void {
    this._onDidChange.fire(undefined);
  }

  getTreeItem(element: vscode.TreeItem): vscode.TreeItem {
    return element;
  }

  async getChildren(
    element?: vscode.TreeItem,
  ): Promise<vscode.TreeItem[]> {
    if (!element) {
      const connected = this.getClient() !== undefined;
      return [new CompanionHeaderItem(connected)];
    }

    if (element instanceof CompanionHeaderItem) {
      const connected = this.getClient() !== undefined;
      if (!connected) {
        return [];
      }
      return CAPABILITIES.map((c) => new CompanionCapabilityItem(c));
    }

    return [];
  }

  dispose(): void {
    this._onDidChange.dispose();
  }
}

// ── Exported helpers ─────────────────────────────────────────

/**
 * Return the system prompt for a companion capability by ID.
 * Returns undefined if the ID is not recognized.
 */
export function getCompanionSystemPrompt(
  capabilityId: string,
): string | undefined {
  return CAPABILITIES.find((c) => c.id === capabilityId)?.systemPrompt;
}

/**
 * Return all registered companion capabilities.
 */
export function getCompanionCapabilities(): readonly CompanionCapability[] {
  return CAPABILITIES;
}
