"""Integration tests for modality detection + step templates in the science loop.

Tests that the autonomous science loop can:
1. Auto-detect input file modalities (video, audio, notebook, code, text)
2. Auto-configure pre-processing steps based on detected modalities
3. Use VideoStep for video files, AudioStep for audio, etc.
4. Handle mixed-modality inputs (e.g., video + text)

See: docs/autonomous-science/DEVELOPMENT_PLAN.md §4.5
"""

from __future__ import annotations

from science.experiment import (
    Experiment,
)


class TestModalityDetection:
    """Test modality detection integration."""

    def test_detect_video_modality(self):
        """Video files are detected as 'movie' modality."""
        from umrs.shared.modality import detect_modality

        assert detect_modality("test.mp4") == "movie"
        assert detect_modality("test.avi") == "movie"
        assert detect_modality("test.mov") == "movie"

    def test_detect_audio_modality(self):
        """Audio files are detected as 'audio' modality."""
        from umrs.shared.modality import detect_modality

        assert detect_modality("test.wav") == "audio"
        assert detect_modality("test.mp3") == "audio"
        assert detect_modality("test.flac") == "audio"

    def test_detect_notebook_modality(self):
        """Notebook files are detected as 'notebook' modality."""
        from umrs.shared.modality import detect_modality

        assert detect_modality("test.ipynb") == "notebook"

    def test_detect_code_modality(self):
        """Code files are detected as 'code' modality."""
        from umrs.shared.modality import detect_modality

        assert detect_modality("test.py") == "code"
        assert detect_modality("test.js") == "code"
        assert detect_modality("test.ts") == "code"

    def test_detect_text_modality(self):
        """Text files are detected as 'text_file' modality."""
        from umrs.shared.modality import detect_modality

        assert detect_modality("test.txt") == "text_file"
        assert detect_modality("test.md") == "text_file"
        assert detect_modality("test.pdf") == "text_file"

    def test_detect_image_modality(self):
        """Image files are detected as 'image' modality."""
        from umrs.shared.modality import detect_modality

        assert detect_modality("test.jpg") == "image"
        assert detect_modality("test.png") == "image"


class TestAutoConfigurePreprocessing:
    """Test auto-configuration of pre-processing steps."""

    def test_video_files_configure_videostep(self):
        """Video input files auto-configure VideoStep pre-processing."""
        exp = Experiment(
            hypothesis_id="h_001",
            protocol={"distance_threshold": 0.05, "video_fps": 2.0},
            input_files=["test1.mp4", "test2.avi"],
        )

        exp.auto_configure_preprocessing()

        assert len(exp.preprocessing_steps) == 1
        step = exp.preprocessing_steps[0]
        assert step["name"] == "video_preprocess"
        assert "VideoStep" in step["entrypoint"]
        assert step["config"]["fps"] == 2.0

    def test_audio_files_configure_audiostep(self):
        """Audio input files auto-configure AudioStep pre-processing."""
        exp = Experiment(
            hypothesis_id="h_001",
            protocol={"audio_language": "ja"},
            input_files=["interview.wav", "podcast.mp3"],
        )

        exp.auto_configure_preprocessing()

        assert len(exp.preprocessing_steps) == 1
        step = exp.preprocessing_steps[0]
        assert step["name"] == "audio_preprocess"
        assert "AudioStep" in step["entrypoint"]
        assert step["config"]["language"] == "ja"

    def test_notebook_files_configure_notebookstep(self):
        """Notebook input files auto-configure NotebookStep pre-processing."""
        exp = Experiment(
            hypothesis_id="h_001",
            protocol={},
            input_files=["analysis.ipynb"],
        )

        exp.auto_configure_preprocessing()

        assert len(exp.preprocessing_steps) == 1
        step = exp.preprocessing_steps[0]
        assert step["name"] == "notebook_preprocess"
        assert "NotebookStep" in step["entrypoint"]

    def test_code_files_configure_codestep(self):
        """Code input files auto-configure CodeStep pre-processing."""
        exp = Experiment(
            hypothesis_id="h_001",
            protocol={},
            input_files=["main.py", "utils.js"],
        )

        exp.auto_configure_preprocessing()

        assert len(exp.preprocessing_steps) == 1
        step = exp.preprocessing_steps[0]
        assert step["name"] == "code_preprocess"
        assert "CodeStep" in step["entrypoint"]

    def test_mixed_modality_configures_multiple_steps(self):
        """Mixed-modality inputs configure multiple pre-processing steps."""
        exp = Experiment(
            hypothesis_id="h_001",
            protocol={"video_fps": 1.0},
            input_files=[
                "video.mp4",  # movie → VideoStep
                "audio.wav",  # audio → AudioStep
                "notebook.ipynb",  # notebook → NotebookStep
                "script.py",  # code → CodeStep
                "notes.txt",  # text_file → no step
            ],
        )

        exp.auto_configure_preprocessing()

        # Should configure 4 pre-processing steps (video, audio, notebook, code)
        step_names = [s["name"] for s in exp.preprocessing_steps]
        assert "video_preprocess" in step_names
        assert "audio_preprocess" in step_names
        assert "notebook_preprocess" in step_names
        assert "code_preprocess" in step_names
        assert len(exp.preprocessing_steps) == 4

    def test_text_files_no_preprocessing(self):
        """Text files don't require pre-processing steps."""
        exp = Experiment(
            hypothesis_id="h_001",
            protocol={},
            input_files=["doc.txt", "paper.pdf", "notes.md"],
        )

        exp.auto_configure_preprocessing()

        # Text files go directly to ingestion — no pre-processing needed
        assert len(exp.preprocessing_steps) == 0

    def test_no_input_files_no_preprocessing(self):
        """No input files means no pre-processing steps."""
        exp = Experiment(
            hypothesis_id="h_001",
            protocol={},
            input_files=[],
        )

        exp.auto_configure_preprocessing()

        assert len(exp.preprocessing_steps) == 0

    def test_existing_preprocessing_not_overwritten(self):
        """If preprocessing_steps is already set, auto_configure is a no-op."""
        exp = Experiment(
            hypothesis_id="h_001",
            protocol={},
            input_files=["video.mp4"],
            preprocessing_steps=[{"name": "custom_step", "entrypoint": "custom:Step"}],
        )

        exp.auto_configure_preprocessing()

        # Should not overwrite existing steps
        assert len(exp.preprocessing_steps) == 1
        assert exp.preprocessing_steps[0]["name"] == "custom_step"


class TestExperimentExecutionWithPreprocessing:
    """Test that Experiment.execute() auto-configures pre-processing."""

    def test_execute_auto_configures_preprocessing(self):
        """execute() calls auto_configure_preprocessing() when input_files exist."""
        exp = Experiment(
            hypothesis_id="h_001",
            protocol={"video_fps": 3.0},
            input_files=["test.mp4"],
        )

        # Execute without a runner — should still auto-configure
        results = exp.execute()

        assert results.succeeded
        assert len(exp.preprocessing_steps) == 1
        assert exp.preprocessing_steps[0]["name"] == "video_preprocess"
        assert exp.preprocessing_steps[0]["config"]["fps"] == 3.0

    def test_execute_without_input_files(self):
        """execute() works fine without input_files."""
        exp = Experiment(
            hypothesis_id="h_001",
            protocol={"threshold": 0.05},
        )

        results = exp.execute()

        assert results.succeeded
        assert len(exp.preprocessing_steps) == 0

    def test_to_dict_includes_preprocessing(self):
        """to_dict() includes input_files and preprocessing_steps."""
        exp = Experiment(
            hypothesis_id="h_001",
            protocol={"video_fps": 2.0},
            input_files=["video.mp4"],
        )
        exp.auto_configure_preprocessing()

        d = exp.to_dict()

        assert d["input_files"] == ["video.mp4"]
        assert len(d["preprocessing_steps"]) == 1
        assert d["preprocessing_steps"][0]["name"] == "video_preprocess"


class TestStepTemplates:
    """Test that the step templates can be imported and configured."""

    def test_videostep_configurable(self):
        """VideoStep can be configured with fps and max_frames."""
        from umrs.pipeline_sdk.templates.video_step import VideoStep

        step = VideoStep()
        step.setup({"fps": 2.0, "max_frames": 50})

        assert step.fps == 2.0
        assert step.max_frames == 50

    def test_audiostep_configurable(self):
        """AudioStep can be configured with language."""
        from umrs.pipeline_sdk.templates.audio_step import AudioStep

        step = AudioStep()
        step.setup({"language": "ja", "model": "whisper-1"})

        assert step.language == "ja"
        assert step.model == "whisper-1"

    def test_notebookstep_configurable(self):
        """NotebookStep can be configured with mode."""
        from umrs.pipeline_sdk.templates.notebook_step import NotebookStep

        step = NotebookStep()
        step.setup({"mode": "cells"})

        assert step.mode == "cells"

    def test_codestep_configurable(self):
        """CodeStep can be configured with language."""
        from umrs.pipeline_sdk.templates.code_step import CodeStep

        step = CodeStep()
        step.setup({"language": "python", "include_imports": True})

        assert step.language == "python"
        assert step.include_imports is True
