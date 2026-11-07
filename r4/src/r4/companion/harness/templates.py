"""Research harness templates — pre-configured bundles for common workflows.

Templates auto-activate based on conversation content so the researcher
simply says what they want to do and the companion self-configures.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class HarnessTemplate:
    """A pre-configured harness bundle for a research workflow type."""

    name: str
    description: str
    # Which skills to activate
    skills: list[str] = field(default_factory=list)
    # Which tool names to prioritize
    tools: list[str] = field(default_factory=list)
    # Sensors to emphasize (informational — all sensors still run)
    sensor_focus: list[str] = field(default_factory=list)
    # Progress milestones to track
    milestones: list[str] = field(default_factory=list)
    # Keywords for auto-detection
    keywords: list[str] = field(default_factory=list)
    # Extra system prompt guidance
    guidance: str = ""

    def matches(self, text: str) -> int:
        """Score how well this template matches the given text."""
        text_lower = text.lower()
        return sum(1 for kw in self.keywords if kw in text_lower)


def get_builtin_templates() -> list[HarnessTemplate]:
    """Return the built-in research harness templates."""
    return [
        HarnessTemplate(
            name="literature_review",
            description="Systematic literature review and synthesis",
            skills=["research"],
            tools=["search_docs", "file_write", "file_read"],
            milestones=[
                "Define research questions",
                "Search and collect papers",
                "Screen and filter",
                "Extract key findings",
                "Synthesize and summarize",
            ],
            keywords=[
                "literature review",
                "survey paper",
                "related work",
                "find papers",
                "search literature",
                "cite",
                "bibliography",
                "systematic review",
            ],
            guidance=(
                "Help the researcher conduct a systematic literature review. "
                "Track papers found, summarize key results, identify gaps, "
                "and organize references. Be thorough but concise."
            ),
        ),
        HarnessTemplate(
            name="experiment_design",
            description="Design and run computational experiments on HPC",
            skills=["hpc", "code"],
            tools=["shell_exec", "python_exec", "hpc_submit", "file_write", "file_read"],
            sensor_focus=["slurm_validation", "resource_check"],
            milestones=[
                "Define experiment parameters",
                "Implement experiment code",
                "Configure HPC submission",
                "Run experiments",
                "Collect results",
                "Analyze results",
            ],
            keywords=[
                "experiment",
                "run simulation",
                "submit job",
                "hpc",
                "cluster",
                "parameter sweep",
                "slurm job",
                "run on the cluster",
            ],
            guidance=(
                "Help the researcher design and run computational experiments. "
                "Always validate Slurm scripts before submission. "
                "Track submitted jobs and their results. "
                "Suggest parameter sweeps when appropriate."
            ),
        ),
        HarnessTemplate(
            name="data_analysis",
            description="Exploratory data analysis and visualization",
            skills=["analysis", "code"],
            tools=["python_exec", "file_read", "file_write"],
            milestones=[
                "Load and inspect data",
                "Clean and preprocess",
                "Exploratory analysis",
                "Create visualizations",
                "Interpret results",
            ],
            keywords=[
                "analyze data",
                "plot",
                "graph",
                "visualization",
                "pandas",
                "dataframe",
                "statistics",
                "explore data",
                "simulation data",
                "results analysis",
            ],
            guidance=(
                "Help the researcher analyze their data. "
                "Start by understanding the data structure and shape. "
                "Suggest appropriate visualizations. "
                "Use pandas/numpy/matplotlib/xarray as appropriate for the data type."
            ),
        ),
        HarnessTemplate(
            name="paper_writing",
            description="Academic paper drafting with LaTeX and SC conference formatting",
            skills=["ml-paper-writing", "systems-paper-writing", "research"],
            tools=[
                "file_read",
                "file_write",
                "file_edit",
                "search_docs",
                "shell_exec",
                "python_exec",
            ],
            milestones=[
                "Define paper scope and target venue",
                "Survey related work (systematic)",
                "Create LaTeX scaffold (IEEEtran/acmart)",
                "Draft abstract and introduction",
                "Write background and related work",
                "Document system design and architecture",
                "Write implementation section",
                "Design and run evaluation/benchmarks",
                "Write evaluation and results",
                "Draft discussion and limitations",
                "Write conclusion and future work",
                "Compile bibliography (BibTeX)",
                "Internal review and polish",
                "Generate camera-ready PDF",
            ],
            keywords=[
                "write paper",
                "draft paper",
                "paper writing",
                "manuscript",
                "latex",
                "abstract",
                "introduction section",
                "conclusion",
                "sc conference",
                "supercomputing",
                "ieeetran",
                "acmart",
                "bibliography",
                "bibtex",
                "related work",
                "camera ready",
                "submit to sc",
                "acm paper",
                "ieee paper",
                "conference paper",
                "journal paper",
            ],
            guidance=(
                "Help the researcher write a conference-quality paper. "
                "SC conference guidelines:\n"
                "- Full papers: up to 10 pages (excluding references) in "
                "IEEEtran or ACM format.\n"
                "- Use \\texttt{booktabs}, avoid vertical rules in tables.\n"
                "- Figures must be readable in grayscale; use distinct "
                "markers/line styles, not just colour.\n"
                "- Artifact Description (AD) appendix encouraged for "
                "reproducibility.\n"
                "- Evaluation must include: experimental setup, metrics, "
                "baselines, statistical significance where applicable.\n\n"
                "Writing process:\n"
                "1. Start with outline → researcher approves.\n"
                "2. Draft sections incrementally; show each before continuing.\n"
                "3. Use search_docs to find and cite prior work.\n"
                "4. Every design claim needs evidence (measurement, citation, "
                "or logical argument).\n"
                "5. Be honest about limitations — SC reviewers value "
                "intellectual honesty.\n"
                "6. Generate BibTeX entries from DOIs/arXiv IDs when possible. "
                "Respect the researcher's writing style. Suggest improvements "
                "rather than rewriting. Track word count against venue limits."
            ),
        ),
        HarnessTemplate(
            name="model_training",
            description="ML model training and evaluation on HPC",
            skills=["hpc", "code"],
            tools=["shell_exec", "python_exec", "hpc_submit", "file_write", "file_read"],
            sensor_focus=["slurm_validation", "gpu_allocation"],
            milestones=[
                "Prepare data",
                "Configure model",
                "Set up training script",
                "Submit training job",
                "Monitor training",
                "Evaluate model",
                "Iterate if needed",
            ],
            keywords=[
                "train model",
                "training",
                "fine-tune",
                "fine-tuning",
                "epoch",
                "loss",
                "checkpoint",
                "model training",
                "gpu training",
            ],
            guidance=(
                "Help the researcher train or fine-tune a model. "
                "Models must be pre-staged (never downloaded at runtime). "
                "Always set TRANSFORMERS_OFFLINE=1 for inference/training jobs. "
                "Track checkpoints and suggest evaluation strategies."
            ),
        ),
        HarnessTemplate(
            name="project_setup",
            description="Set up a new research project",
            skills=["project_setup", "code"],
            tools=["shell_exec", "file_write", "file_read", "list_dir"],
            milestones=[
                "Create project structure",
                "Configure environment",
                "Set up version control",
                "Document project",
            ],
            keywords=[
                "new project",
                "set up project",
                "create project",
                "scaffold",
                "initialize",
                "project structure",
                "boilerplate",
            ],
            guidance=(
                "Help the researcher set up a new project. "
                "Create a clean directory structure with README, "
                "pyproject.toml (or Makefile), data/ and src/ directories. "
                "Initialize git. Set up a virtual environment."
            ),
        ),
    ]


def detect_template(
    text: str, templates: list[HarnessTemplate] | None = None
) -> HarnessTemplate | None:
    """Auto-detect the best matching template for the given text.

    Returns the template if any matches with ≥2 keyword hits, else None.
    """
    templates = templates or get_builtin_templates()
    best: HarnessTemplate | None = None
    best_score = 0

    for t in templates:
        score = t.matches(text)
        if score > best_score:
            best_score = score
            best = t

    if best_score >= 2:
        logger.info("Auto-detected template: %s (score=%d)", best.name, best_score)
        return best
    return None
