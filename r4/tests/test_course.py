"""E3 — ``r4 course`` university course-kit scaffolder."""

from __future__ import annotations

from argparse import Namespace

import pytest

from r4.course import build_course_assets, materialize_course


@pytest.fixture()
def tmp_course(tmp_path):
    return tmp_path / "course-pack"


def _ns(directory, **kw):
    base = dict(
        course_action="create",
        directory=directory,
        title="Computational Science Lab",
        title_ja="計算科学実習",
        lang="both",
        force=False,
        as_json=False,
    )
    base.update(kw)
    return Namespace(**base)


class TestAssetBuilder:
    def test_build_course_assets_renders_both_languages(self):
        a = build_course_assets(title="X", title_ja="X日本語")
        assert "X" in a.syllabus_en
        assert "X日本語" in a.syllabus_ja
        assert "course_title_jp" not in a.syllabus_en  # JA key never seen in EN template

    def test_default_title_ja_falls_back_to_title(self):
        a = build_course_assets(title="X", title_ja=None)
        assert a.course_yaml["title_ja"] == "X"


class TestMaterializeCourse:
    def test_default_tree_layout(self, tmp_course):
        written = materialize_course(
            tmp_course, title="Computational Science Lab", title_ja="計算科学実習"
        )
        assert (tmp_course / "SYLLABUS.md").exists()
        assert (tmp_course / "SYLLABUS.ja.md").exists()
        assert (tmp_course / "README.md").exists()
        assert (tmp_course / "README.ja.md").exists()
        assert (tmp_course / "course.yaml").exists()
        assert (tmp_course / ".r4ignore").exists()
        for assi_id in ("1", "2"):
            for suffix in ("README.md", "README.ja.md"):
                assert (tmp_course / "assignments" / f"AS{assi_id}" / suffix).exists()
        assert len(written) == 10

    def test_lang_en_only_skips_ja_files(self, tmp_course):
        materialize_course(tmp_course, title="X", title_ja=None, lang="en")
        assert (tmp_course / "SYLLABUS.md").exists()
        assert not (tmp_course / "SYLLABUS.ja.md").exists()
        # assignment dirs ship both README and README.ja.md per flag; for
        # lang=en only the EN READMEs land.
        assert (tmp_course / "assignments" / "AS1" / "README.md").exists()
        assert not (tmp_course / "assignments" / "AS1" / "README.ja.md").exists()

    def test_lang_ja_only_skips_en_files(self, tmp_course):
        materialize_course(tmp_course, title="X", title_ja="X日本語", lang="ja")
        assert not (tmp_course / "SYLLABUS.md").exists()
        assert (tmp_course / "SYLLABUS.ja.md").exists()
        assert (tmp_course / "assignments" / "AS1" / "README.ja.md").exists()
        assert not (tmp_course / "assignments" / "AS1" / "README.md").exists()


class TestCLI:
    def test_course_create_smoke(self, tmp_path, capsys):
        from r4._cli.course import _cmd_course_create

        out_dir = tmp_path / "course-1"
        rc = _cmd_course_create(_ns(str(out_dir)))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Files: 10" in out
        assert (out_dir / "SYLLABUS.md").exists()
        assert (out_dir / "assignments" / "AS1" / "README.md").exists()

    def test_course_create_json_output(self, tmp_path, capsys):
        from r4._cli.course import _cmd_course_create

        rc = _cmd_course_create(_ns(str(tmp_path / "c2"), as_json=True))
        assert rc == 0
        import json as _json

        payload = _json.loads(capsys.readouterr().out)
        assert payload["lang"] == "both"
        assert len(payload["files"]) == 10

    def test_refuses_nonempty_dir_without_force(self, tmp_path):
        from r4._cli.course import _cmd_course_create

        target = tmp_path / "occupied"
        target.mkdir()
        (target / "junk.txt").write_text("x")
        with pytest.raises(SystemExit):
            _cmd_course_create(_ns(str(target)))

    def test_force_allows_nonempty_dir(self, tmp_path, capsys):
        from r4._cli.course import _cmd_course_create

        target = tmp_path / "occupied"
        target.mkdir()
        (target / "junk.txt").write_text("x")
        rc = _cmd_course_create(_ns(str(target), force=True))
        assert rc == 0

    def test_unknown_lang_dies(self, tmp_path):
        from r4._cli.course import _cmd_course_create

        with pytest.raises(SystemExit):
            _cmd_course_create(_ns(str(tmp_path / "c3"), lang="fr"))
