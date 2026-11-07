"""Tests for harness components: probe, sensors, progress, steering, templates."""

from __future__ import annotations

# -- EnvironmentProbe tests ------------------------------------------------


class TestEnvironmentProbe:
    def test_probe_cwd(self, tmp_path, monkeypatch):
        from r4.companion.harness.probe import EnvironmentProbe

        # The assertion is ABOUT the snapshot shape of a bare cwd — the host
        # itself must not leak an HPC marker into it (dev boxes can carry
        # slurm/apptainer on PATH and the test would then die flakily by
        # machine trait, never by intent). Pin the external dependence to
        # "nothing present" so behavior, not host, is the variable.
        monkeypatch.setattr("shutil.which", lambda _n: None)
        monkeypatch.delenv("MODULESHOME", raising=False)
        monkeypatch.delenv("LOADEDMODULES", raising=False)

        probe = EnvironmentProbe()
        snap = probe.run(cwd=str(tmp_path))
        assert snap.cwd == str(tmp_path)
        assert not snap.has_readme
        assert not snap.is_hpc

    def test_probe_detects_readme(self, tmp_path):
        from r4.companion.harness.probe import EnvironmentProbe

        (tmp_path / "README.md").write_text("# My Project\nThis is a test project.")
        probe = EnvironmentProbe()
        snap = probe.run(cwd=str(tmp_path))
        assert snap.has_readme
        assert "My Project" in snap.readme_summary

    def test_probe_detects_pyproject(self, tmp_path):
        from r4.companion.harness.probe import EnvironmentProbe

        (tmp_path / "pyproject.toml").write_text('[project]\nname = "testpkg"\n')
        probe = EnvironmentProbe()
        snap = probe.run(cwd=str(tmp_path))
        assert snap.has_pyproject
        assert snap.project_name == "testpkg"

    def test_probe_detects_data_dirs(self, tmp_path):
        from r4.companion.harness.probe import EnvironmentProbe

        (tmp_path / "data").mkdir()
        (tmp_path / "results").mkdir()
        probe = EnvironmentProbe()
        snap = probe.run(cwd=str(tmp_path))
        assert "data" in snap.data_dirs
        assert "results" in snap.data_dirs

    def test_probe_detects_makefile(self, tmp_path):
        from r4.companion.harness.probe import EnvironmentProbe

        (tmp_path / "Makefile").write_text("build:\n\techo build\ntest:\n\techo test\n")
        probe = EnvironmentProbe()
        snap = probe.run(cwd=str(tmp_path))
        assert snap.has_makefile
        assert "build" in snap.make_targets

    def test_as_prompt(self, tmp_path):
        from r4.companion.harness.probe import EnvironmentProbe, EnvironmentSnapshot

        snap = EnvironmentSnapshot(cwd=str(tmp_path), project_name="testpkg")
        prompt = EnvironmentProbe.as_prompt(snap)
        assert "testpkg" in prompt
        assert "Working directory" in prompt


# -- QualitySensor tests ---------------------------------------------------


class TestQualitySensor:
    def test_pre_check_rm_rf(self):
        from r4.companion.harness.sensors import QualitySensor

        sensor = QualitySensor()
        result = sensor.pre_execution("shell_exec", {"command": "rm -rf /"})
        assert result is not None
        assert "BLOCKED" in result

    def test_pre_check_model_download(self):
        from r4.companion.harness.sensors import QualitySensor

        sensor = QualitySensor()
        result = sensor.pre_execution(
            "shell_exec", {"command": "wget https://huggingface.co/model"}
        )
        assert result is not None
        assert "model download" in result.lower() or "stage" in result.lower()

    def test_pre_check_python_syntax(self):
        from r4.companion.harness.sensors import QualitySensor

        sensor = QualitySensor()
        result = sensor.pre_execution("python_exec", {"code": "def foo(\n"})
        assert result is not None
        assert "Syntax" in result

    def test_pre_check_valid_python(self):
        from r4.companion.harness.sensors import QualitySensor

        sensor = QualitySensor()
        result = sensor.pre_execution("python_exec", {"code": "print('hello')"})
        assert result is None

    def test_pre_check_file_read_missing(self, tmp_path):
        from r4.companion.harness.sensors import QualitySensor

        sensor = QualitySensor()
        result = sensor.pre_execution("file_read", {"path": str(tmp_path / "nonexistent.txt")})
        assert result is not None
        assert "not found" in result

    def test_post_check_repeated_errors(self):
        from r4.companion.harness.sensors import QualitySensor

        sensor = QualitySensor()
        for i in range(3):
            result = sensor.post_execution(
                "shell_exec", {"command": "test"}, "error: something failed"
            )
        assert result is not None
        assert "different approach" in result

    def test_post_check_command_not_found(self):
        from r4.companion.harness.sensors import QualitySensor

        sensor = QualitySensor()
        result = sensor.post_execution(
            "shell_exec", {"command": "myapp"}, "bash: myapp: command not found"
        )
        assert result is not None
        assert "module" in result.lower() or "not found" in result.lower()

    def test_pre_check_hpc_submit(self, tmp_path):
        from r4.companion.harness.sensors import QualitySensor

        script = tmp_path / "job.sh"
        script.write_text(
            "#!/bin/bash\n"
            "#SBATCH --gres=gpu:8\n"
            "#SBATCH --time=72:00:00\n"
            "# Run vllm inference\n"
            "python run_inference.py --model 7B\n"
        )
        sensor = QualitySensor()
        result = sensor.pre_execution("hpc_submit", {"script_path": str(script)})
        assert result is not None
        # Should flag: GPU over-allocation for 7B, time limit >48h, missing TRANSFORMERS_OFFLINE
        assert "GPU" in result or "gpu" in result.lower()


# -- ProgressTracker tests -------------------------------------------------


class TestProgressTracker:
    def test_record_turn(self, tmp_path):
        from r4.companion.harness.progress import ProgressTracker

        tracker = ProgressTracker(progress_dir=tmp_path)
        tracker.record_turn("What is X?", "X is Y.")
        assert tracker._state.turn_count == 1

    def test_milestones(self, tmp_path):
        from r4.companion.harness.progress import ProgressTracker

        tracker = ProgressTracker(progress_dir=tmp_path)
        tracker.add_milestone("Step 1")
        tracker.add_milestone("Step 2")
        tracker.update_milestone("Step 1", "done", notes="Completed")
        # Record a turn so orientation produces output
        tracker.record_turn("test", "result")

        orientation = tracker.orientation()
        assert orientation is not None
        assert "✓" in orientation
        assert "Step 1" in orientation
        assert "○" in orientation  # Step 2 still pending

    def test_orientation_empty(self, tmp_path):
        from r4.companion.harness.progress import ProgressTracker

        tracker = ProgressTracker(progress_dir=tmp_path)
        assert tracker.orientation() is None

    def test_generate_summary(self, tmp_path):
        from r4.companion.harness.progress import ProgressTracker

        tracker = ProgressTracker(progress_dir=tmp_path)
        messages = [
            {"role": "user", "content": "Analyze earthquake data"},
            {"role": "assistant", "content": "I've created the analysis script."},
        ]
        summary = tracker.generate_summary(messages)
        assert "summary" in summary
        assert "total_turns" in summary

    def test_artifact_extraction(self, tmp_path):
        from r4.companion.harness.progress import ProgressTracker

        tracker = ProgressTracker(progress_dir=tmp_path)
        tracker.record_turn("Submit job", "Submitted batch job 12345")
        assert "slurm:12345" in tracker._state.artifacts

    def test_persistence(self, tmp_path):
        from r4.companion.harness.progress import ProgressTracker

        tracker = ProgressTracker(progress_dir=tmp_path)
        tracker.add_milestone("Persistent step")
        tracker.record_turn("Test", "Response")

        # Reload
        tracker2 = ProgressTracker(progress_dir=tmp_path)
        assert tracker2._state.turn_count == 1
        assert len(tracker2._state.milestones) == 1


# -- SteeringLoop tests ----------------------------------------------------


class TestSteeringLoop:
    def test_observe_correction(self, tmp_path):
        from r4.companion.harness.steering import SteeringLoop

        steering = SteeringLoop(lessons_dir=tmp_path)
        # First turn — agent responds
        steering.observe_turn("Help me", "Use wget to download the model")
        # Second turn — user corrects
        steering.observe_turn("No, use r4 model stage instead", "OK, I'll use r4 model stage.")

        assert len(steering.list_lessons()) == 1
        assert "r4 model stage" in steering.list_lessons()[0].rule.lower()

    def test_no_lesson_without_correction(self, tmp_path):
        from r4.companion.harness.steering import SteeringLoop

        steering = SteeringLoop(lessons_dir=tmp_path)
        steering.observe_turn("Hello", "Hi there!")
        steering.observe_turn("How are you?", "I'm good.")
        assert len(steering.list_lessons()) == 0

    def test_lessons_as_prompt(self, tmp_path):
        from r4.companion.harness.steering import SteeringLoop

        steering = SteeringLoop(lessons_dir=tmp_path)
        steering.add_lesson(
            context="training job",
            mistake="used wget",
            correction="use r4 model stage",
            rule="Use r4 model stage for model downloads",
        )
        prompt = steering.as_prompt()
        assert prompt is not None
        assert "model stage" in prompt

    def test_anti_pattern_detection(self, tmp_path):
        from r4.companion.harness.steering import SteeringLoop

        steering = SteeringLoop(lessons_dir=tmp_path)
        result = steering.check_anti_patterns("wget https://huggingface.co/model/download")
        assert result is not None
        assert "r4 model stage" in result

    def test_anti_pattern_clean(self, tmp_path):
        from r4.companion.harness.steering import SteeringLoop

        steering = SteeringLoop(lessons_dir=tmp_path)
        result = steering.check_anti_patterns("Hello world")
        assert result is None

    def test_lesson_persistence(self, tmp_path):
        from r4.companion.harness.steering import SteeringLoop

        steering = SteeringLoop(lessons_dir=tmp_path)
        steering.add_lesson("ctx", "mistake", "correction", "Never do X")

        steering2 = SteeringLoop(lessons_dir=tmp_path)
        assert len(steering2.list_lessons()) == 1


# -- HarnessTemplate tests ------------------------------------------------


class TestHarnessTemplates:
    def test_builtin_templates_exist(self):
        from r4.companion.harness.templates import get_builtin_templates

        templates = get_builtin_templates()
        names = [t.name for t in templates]
        assert "literature_review" in names
        assert "experiment_design" in names
        assert "data_analysis" in names
        assert "model_training" in names

    def test_detect_template_data_analysis(self):
        from r4.companion.harness.templates import detect_template

        t = detect_template("I need to analyze my simulation data and create some plots")
        assert t is not None
        assert t.name == "data_analysis"

    def test_detect_template_experiment(self):
        from r4.companion.harness.templates import detect_template

        t = detect_template("Help me submit a slurm job to run my experiment on the cluster")
        assert t is not None
        assert t.name == "experiment_design"

    def test_detect_template_no_match(self):
        from r4.companion.harness.templates import detect_template

        t = detect_template("Hello, how are you today?")
        assert t is None

    def test_template_milestones(self):
        from r4.companion.harness.templates import get_builtin_templates

        for t in get_builtin_templates():
            assert len(t.milestones) > 0, f"Template {t.name} has no milestones"
            assert len(t.skills) > 0, f"Template {t.name} has no skills"


class TestHarnessFeedforwardTemplates:
    def test_feedforward_includes_template(self):
        from unittest.mock import MagicMock

        from r4.companion.harness import ResearchHarness
        from r4.companion.session_models import SessionMessage

        harness = ResearchHarness(
            client=MagicMock(),
            context_manager=MagicMock(),
            skill_registry=MagicMock(),
        )
        messages = [
            SessionMessage(
                role="user", content="I need a literature review and systematic review on quantum"
            ),
            SessionMessage(role="assistant", content="Sure!"),
        ]
        result = harness.feedforward(messages)
        assert "[Workflow:" in result
        assert "literature_review" in result

    def test_feedforward_no_template_for_generic(self):
        from unittest.mock import MagicMock

        from r4.companion.harness import ResearchHarness
        from r4.companion.session_models import SessionMessage

        harness = ResearchHarness(
            client=MagicMock(),
            context_manager=MagicMock(),
            skill_registry=MagicMock(),
        )
        messages = [
            SessionMessage(role="user", content="Hello there"),
            SessionMessage(role="assistant", content="Hi!"),
        ]
        result = harness.feedforward(messages)
        assert "literature_review" not in result
        assert "experiment_design" not in result


class TestHarnessTemplateCommands:
    def test_apply_template_activates_skills_and_milestones(self, tmp_path):
        from unittest.mock import MagicMock

        from r4.companion.harness import ResearchHarness
        from r4.companion.harness.progress import ProgressTracker
        from r4.companion.skills import SkillRegistry

        registry = SkillRegistry()
        harness = ResearchHarness(
            client=MagicMock(),
            context_manager=MagicMock(),
            skill_registry=registry,
        )
        harness.progress = ProgressTracker(progress_dir=tmp_path)

        result = harness.apply_template("data_analysis")

        assert result["template"] == "data_analysis"
        assert "analysis" in {skill.name for skill in registry.list_active()}
        assert len(harness.progress._state.milestones) == 5
        assert harness.active_template() is not None
        assert harness.active_template().name == "data_analysis"

    def test_active_template_overrides_auto_detect(self):
        from unittest.mock import MagicMock

        from r4.companion.harness import ResearchHarness
        from r4.companion.session_models import SessionMessage

        harness = ResearchHarness(
            client=MagicMock(),
            context_manager=MagicMock(),
            skill_registry=MagicMock(),
        )
        harness.apply_template("project_setup")
        messages = [
            SessionMessage(
                role="user",
                content="I need a literature review and systematic review on quantum",
            ),
            SessionMessage(role="assistant", content="Sure!"),
        ]
        result = harness.feedforward(messages)
        assert "project_setup" in result
        assert "literature_review" not in result

    def test_clear_active_template_restores_auto_detect(self):
        from unittest.mock import MagicMock

        from r4.companion.harness import ResearchHarness
        from r4.companion.session_models import SessionMessage

        harness = ResearchHarness(
            client=MagicMock(),
            context_manager=MagicMock(),
            skill_registry=MagicMock(),
        )
        harness.apply_template("project_setup")
        harness.clear_active_template()
        messages = [
            SessionMessage(
                role="user",
                content="I need a literature review and systematic review on quantum",
            ),
            SessionMessage(role="assistant", content="Sure!"),
        ]
        result = harness.feedforward(messages)
        assert "literature_review" in result
        assert "project_setup" not in result


class TestHarnessAblation:
    """Benchmark-only ablation gates (R4_HARNESS_ABLATE_* env flags).

    Off by default; the bench runner flips them per cell to measure the
    steering/guides/memory layers' contribution (harness-benchmark plan).
    """

    def _harness(self):
        from unittest.mock import MagicMock

        from r4.companion.harness import ResearchHarness

        return ResearchHarness(
            client=MagicMock(),
            context_manager=MagicMock(),
            skill_registry=MagicMock(),
        )

    @staticmethod
    def _seed_lesson(harness) -> None:
        harness.steering.add_lesson(
            context="ablation test",
            mistake="ran tests against a live gateway",
            correction="use the local stand-in",
            rule="never call a live gateway from a unit test",
        )

    def test_flags_default_off(self, monkeypatch):
        from r4.companion import ablation

        for var in (ablation.ABLATE_STEERING, ablation.ABLATE_GUIDES, ablation.ABLATE_MEMORY):
            monkeypatch.delenv(var, raising=False)
        assert not ablation.steering_off()
        assert not ablation.guides_off()
        assert not ablation.memory_off()

    def test_steering_off_removes_lessons(self, monkeypatch):
        from r4.companion import ablation
        from r4.companion.session_models import SessionMessage

        monkeypatch.setenv(ablation.ABLATE_STEERING, "1")
        harness = self._harness()
        # Lessons come from storage only; they used to be read from the
        # developer's real ~/.config/r4 (2026-10-17). Seed one.
        self._seed_lesson(harness)
        lessons = harness.steering.as_prompt()
        assert lessons
        messages = [SessionMessage(role="user", content="hello")]
        assert lessons not in harness.feedforward(messages)

    def test_steering_on_keeps_lessons(self, monkeypatch):
        from r4.companion import ablation
        from r4.companion.session_models import SessionMessage

        monkeypatch.delenv(ablation.ABLATE_STEERING, raising=False)
        harness = self._harness()
        self._seed_lesson(harness)
        lessons = harness.steering.as_prompt()
        assert lessons
        messages = [SessionMessage(role="user", content="hello")]
        assert lessons in harness.feedforward(messages)

    def test_guides_off_removes_domain_and_template(self, monkeypatch):
        from r4.companion import ablation
        from r4.companion.session_models import SessionMessage

        monkeypatch.setenv(ablation.ABLATE_GUIDES, "1")
        harness = self._harness()
        messages = [
            SessionMessage(
                role="user",
                content="I need a literature review and systematic review on quantum",
            ),
            SessionMessage(role="assistant", content="Sure!"),
        ]
        result = harness.feedforward(messages)
        assert "[Workflow:" not in result
        assert "literature_review" not in result

    def test_steering_off_skips_observe_turn(self, monkeypatch):
        from unittest.mock import MagicMock

        from r4.companion import ablation

        monkeypatch.setenv(ablation.ABLATE_STEERING, "1")
        harness = self._harness()
        harness.steering = MagicMock()
        harness.post_turn(user_message="hi", response="hello", messages=[])
        harness.steering.observe_turn.assert_not_called()
        harness.progress.record_turn("a", "b")  # progress layer stays on
