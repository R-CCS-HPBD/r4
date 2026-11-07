"""D4 — OIDC provider preset tests (GakuNin / ORCID / GitHub)."""

from __future__ import annotations

from argparse import Namespace

import pytest

from r4.oidc_providers import apply_provider, get_provider, list_providers


class TestProviderRegistry:
    def test_three_presets_available(self):
        names = {p.name for p in list_providers()}
        assert names == {"gakunin", "orcid", "github"}

    def test_gakunin_uses_test_op_issuer(self):
        p = get_provider("gakunin")
        assert "gakunin.nii.ac.jp" in p.issuer
        assert "openid" in p.scope

    def test_orcid_uses_official_issuer(self):
        p = get_provider("orcid")
        assert p.issuer == "https://orcid.org"

    def test_github_oauth_issuer(self):
        p = get_provider("github")
        assert p.issuer.startswith("https://github.com/login/oauth")

    def test_aliases_resolve(self):
        assert get_provider("nii").name == "gakunin"
        assert get_provider("gh").name == "github"

    def test_unknown_raises_with_known_list(self):
        with pytest.raises(KeyError, match="gakunin"):
            get_provider("no-such")

    def test_every_preset_has_docs_and_hint(self):
        for p in list_providers():
            assert p.registration_hint.strip()
            assert p.docs_url.startswith("https://")


class TestApplyProvider:
    def test_env_from_preset(self):
        p = get_provider("gakunin")
        env, overlay = apply_provider(p, client_id="cid-1")
        assert env["R4_OIDC_ISSUER"] == p.issuer
        assert env["R4_OIDC_CLIENT_ID"] == "cid-1"
        assert overlay["oidc_issuer"] == p.issuer
        assert overlay["oidc_scope"] == p.scope

    def test_client_id_required(self):
        with pytest.raises(ValueError, match="client_id"):
            apply_provider(get_provider("orcid"), client_id=None)


class _NS:
    def __init__(self, **kw):
        base = dict(
            provider=None,
            oidc=False,
            oidc_issuer=None,
            oidc_scope=None,
            oidc_client_id=None,
        )
        base.update(kw)
        self._ns = Namespace(**base)

    @property
    def ns(self):
        return self._ns


class TestCLIProviderFlag:
    def _apply(self, **kw):
        from r4._cli.login import _apply_provider_preset

        n = _NS(**kw).ns
        _apply_provider_preset(n)
        return n

    def test_provider_fills_issuer_and_scope(self):
        n = self._apply(provider="gakunin", oidc_client_id="cid-1")
        assert n.oidc_issuer == get_provider("gakunin").issuer
        assert n.oidc_scope == get_provider("gakunin").scope
        assert n.oidc is True

    def test_provider_implies_oidc_without_flag(self):
        n = self._apply(provider="orcid", oidc_client_id="cid-2")
        assert n.oidc is True

    def test_explicit_issuer_wins_over_preset(self):
        n = self._apply(
            provider="github",
            oidc_issuer="https://custom.example.com",
            oidc_client_id="cid-3",
        )
        assert n.oidc_issuer == "https://custom.example.com"

    def test_unknown_provider_dies(self):
        with pytest.raises(SystemExit):
            self._apply(provider="no-such", oidc_client_id="x")

    def test_missing_client_id_dies(self):
        with pytest.raises(SystemExit):
            self._apply(provider="gakunin")

    def test_no_provider_is_noop(self):
        n = self._apply()
        assert n.oidc_issuer is None
        assert n.oidc is False


class TestProfileStoreScope:
    def test_login_writes_oidc_scope_to_profile(self, tmp_path, monkeypatch):
        """_save_login_profile_settings persists oidc_scope from args."""
        from r4._cli.login import _save_login_profile_settings

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        n = Namespace(
            gateway_url=None,
            project=None,
            model=None,
            umrs_username=None,
            oidc_issuer="https://orcid.org",
            oidc_client_id="cid",
            oidc_scope="/openid",
            oidc=True,
            profile="d4test",
        )
        _save_login_profile_settings(n)

        from r4.config import ProfileConfigStore

        stored = ProfileConfigStore().load("d4test")
        assert stored["oidc_issuer"] == "https://orcid.org"
        assert stored["oidc_scope"] == "/openid"
