"""Named OIDC provider presets for the research-identity login flow (D4).

Japanese academic deployments mostly authenticate through these IdPs:
- **GakuNin**   — NII-academic federation (SAML/mAP); their document-relay
                  OIDC test OP is the standard dev/test entry point
- **ORCID**     — researcher identity (public API client allowed by ORCID)
- **GitHub**    — developer identity (for repo-bound research assets)

A preset is *just* the OIDC issuer + default scopes + a registration hint;
the client_id (and any client_secret) must still come from the user, since
every deployment registers its own app with each IdP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class OIDCProvider:
    """A named OIDC provider preset."""

    name: str
    issuer: str
    scope: str
    registration_hint: str
    docs_url: str
    aliases: tuple[str, ...] = field(default_factory=tuple)


_PROVIDERS: dict[str, OIDCProvider] = {
    "gakunin": OIDCProvider(
        name="gakunin",
        issuer="https://oid-test.op.core.gakunin.nii.ac.jp/oidc/",
        scope="openid profile email",
        registration_hint=(
            "GakuNin Relying-Party registration is required for a production "
            "client_id — see their Relying Party v2 manual. The test OP "
            "accepts self-registrations for development."
        ),
        docs_url="https://meatwiki.nii.ac.jp/confluence/pages/viewpage.action?pageId=46952088",
        aliases=("nii", "gakunin-tap"),
    ),
    "orcid": OIDCProvider(
        name="orcid",
        issuer="https://orcid.org",
        scope="/openid",
        registration_hint=(
            "Register a Public API client at https://orcid.org/developer-tools"
            "; public API is free for non-commercial use."
        ),
        docs_url="https://info.orcid.org/documentation/api-tutorials/api-tutorial-get-and-authenticated-orcid-id/",
        aliases=("orcid.org",),
    ),
    "github": OIDCProvider(
        name="github",
        issuer="https://github.com/login/oauth",
        scope="read:user user:email",
        registration_hint=(
            "Register a GitHub OAuth App in your org/user settings; PKCE is "
            "not required by GitHub but the r4 flow sends it anyway."
        ),
        docs_url="https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps",
        aliases=("gh",),
    ),
}


def list_providers() -> list[OIDCProvider]:
    """Return every preset in stable order."""
    return sorted(_PROVIDERS.values(), key=lambda p: p.name)


def get_provider(name: str) -> OIDCProvider:
    """Resolve a provider by canonical name or alias."""
    key = name.strip().lower()
    for provider in _PROVIDERS.values():
        if key == provider.name or key in provider.aliases:
            return provider
    known = ", ".join(p.name for p in list_providers())
    raise KeyError(f"unknown OIDC provider: {name!r} (known: {known})")


def apply_provider(provider: OIDCProvider, *, client_id: Optional[str]) -> tuple[dict, dict]:
    """Produce (env_for_login, profile_store_overlay) from a preset + client_id.

    The caller merges ``env_for_login`` into the login-process environment and
    ``profile_store_overlay`` into its ProfileConfigStore payload, so subsequent
    commands resolve the same OIDC config without repeating the preset.
    """
    if not client_id:
        raise ValueError("client_id is required for OIDC login via a preset")
    env = {
        "R4_OIDC_ISSUER": provider.issuer,
        "R4_OIDC_CLIENT_ID": client_id,
        "R4_OIDC_SCOPE": provider.scope,
    }
    overlay = {
        "oidc_issuer": provider.issuer,
        "oidc_client_id": client_id,
        "oidc_scope": provider.scope,
    }
    return env, overlay
