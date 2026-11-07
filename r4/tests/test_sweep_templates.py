"""D2 wave — sweep template rendering tests (no cluster submission)."""

from __future__ import annotations

import json

import pytest

from r4.sweep_templates import (
    SweepTemplate,
    expand_grid,
    list_templates,
    load_template,
    render_plan,
    write_bundle,
)


class TestTemplateLoading:
    def test_builtin_pack_has_both_templates(self):
        names = {t.name for t in list_templates()}
        assert {"llm-bench", "generic-mpi"} <= names

    def test_llm_bench_fields(self):
        t = load_template("llm-bench")
        # no site partition baked in: the cluster default applies unless set
        assert t.defaults["partition"] == ""
        assert "model" in t.required_params()
        assert set(["model", "suite", "max_tokens", "gateway"]) == set(t.all_param_names())

    def test_generic_mpi_required_params(self):
        t = load_template("generic-mpi")
        assert set(t.required_params()) == {"ntasks", "cmd"}

    def test_missing_template_raises(self):
        with pytest.raises(FileNotFoundError):
            load_template("does-not-exist")

    def test_from_dict_rejects_no_script(self):
        with pytest.raises(ValueError, match="script"):
            SweepTemplate.from_dict({"name": "x", "script": "  "})


class TestExpandGrid:
    def test_cartesian_grid_expansion(self):
        combos = expand_grid(grid={"a": [1, 2], "b": ["x", "y"]})
        assert combos == [
            {"a": 1, "b": "x"},
            {"a": 1, "b": "y"},
            {"a": 2, "b": "x"},
            {"a": 2, "b": "y"},
        ]

    def test_explicit_combos_passed_through(self):
        combos = expand_grid(combos=[{"a": 1}])
        assert combos == [{"a": 1}]

    def test_max_jobs_enforced(self):
        with pytest.raises(ValueError, match="max_jobs"):
            expand_grid(grid={"a": list(range(300))}, max_jobs=16)

    def test_empty_grid_rejected(self):
        with pytest.raises(ValueError):
            expand_grid(grid=None, combos=None)


class TestRenderPlan:
    def test_render_substitutes_params_and_records_job_names(self, tmp_path):
        t = load_template("llm-bench")
        combos = [{"model": "m1", "suite": "unit_test"}, {"model": "m2", "suite": "japanese"}]
        plan = render_plan(t, combos)

        assert [j.job_name for j in plan] == [
            "r4sweep-llm-bench-001",
            "r4sweep-llm-bench-002",
        ]
        assert '--models "m1"' in plan[0].script
        assert '--categories "unit_test"' in plan[0].script
        assert '--categories "japanese"' in plan[1].script
        # defaults from template merged into script
        assert "--time=02:00:00" in plan[0].script

    def test_unset_partition_drops_the_sbatch_line(self):
        t = load_template("llm-bench")
        plan = render_plan(t, [{"model": "m1"}])
        assert "--partition" not in plan[0].script
        assert "#SBATCH --time=02:00:00" in plan[0].script

    def test_combo_overrides_defaults(self):
        t = load_template("llm-bench")
        plan = render_plan(t, [{"model": "m1", "suite": "s", "partition": "gpu-a"}])
        assert "#SBATCH --partition=gpu-a\n" in plan[0].script

    def test_only_empty_sbatch_options_are_dropped(self):
        t = SweepTemplate(
            name="opts",
            params={"a": {"default": ""}, "b": {"default": "x"}},
            script="#SBATCH --account={{a}}\n#SBATCH --qos={{b}}\necho {{a}}done\n",
        )
        script = render_plan(t, [{}])[0].script
        assert script == "#SBATCH --qos=x\necho done\n"

    def test_bench_requires_r4_src_instead_of_a_home_default(self):
        script = load_template("llm-bench").script
        assert "${R4_SRC:?" in script
        assert "ai4s-platform" not in script

    def test_missing_required_param_rejected(self):
        t = load_template("llm-bench")
        with pytest.raises(ValueError, match="model"):
            render_plan(t, [{"suite": "unit_test"}])

    def test_unresolved_placeholder_rejected(self):
        t = SweepTemplate(
            name="bad",
            params={"known": {"default": "ok"}},
            script="echo {{known}} {{unknown}}",
        )
        with pytest.raises(ValueError, match="unresolved"):
            render_plan(t, [{"known": "k"}])

    def test_write_bundle_creates_scripts_and_manifest(self, tmp_path):
        t = load_template("llm-bench")
        plan = render_plan(t, [{"model": "m1", "suite": "s"}])
        out = write_bundle(plan, tmp_path / "bundle")

        script = (out["jobs_dir"] / "r4sweep-llm-bench-001.sbatch").read_text()
        assert '--models "m1"' in script

        manifest = json.loads((out["manifest"]).read_text())
        assert manifest["jobs"][0]["job_name"] == "r4sweep-llm-bench-001"
        assert manifest["jobs"][0]["params"]["model"] == "m1"


class TestCLIParserSetup:
    def test_sweep_group_registered(self):
        import r4.cli as cli_mod

        registry = cli_mod._load_dispatch_registry()
        assert "sweep" in registry

    def _ns(self, **kw):
        from argparse import Namespace

        base = dict(
            sweep_action="render",
            template="llm-bench",
            param=None,
            combos=None,
            max_jobs=256,
            job_name_prefix="r4sweep",
            out=None,
            json=False,
        )
        base.update(kw)
        return Namespace(**base)

    def test_render_cli_writes_bundle_and_prints(self, tmp_path, capsys):
        from r4._cli.sweep import _render

        args = self._ns(out=str(tmp_path / "out"), param=["model=m1,m2"])
        rc = _render(args)
        assert rc == 0
        out_text = capsys.readouterr().out
        assert "2 job script(s)" in out_text
        assert (tmp_path / "out" / "manifest.json").exists()

    def test_unknown_template_dies_with_error_id(self, tmp_path):
        from r4._cli.sweep import _render

        args = self._ns(out=str(tmp_path), template="missing", param=["x=y"])
        with pytest.raises(SystemExit):
            _render(args)
