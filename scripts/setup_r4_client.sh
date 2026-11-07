#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./scripts/setup_r4_client.sh

Create a user-local virtual environment for the r4 CLI, install the local
package from this repository, and write a wrapper command to ~/.local/bin/r4.

Environment variables:
  R4_CLIENT_HOME     Override the install root (default: ~/.local/share/r4-client)
  R4_CLIENT_BIN_DIR  Override the wrapper directory (default: ~/.local/bin)
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
R4_DIR="${REPO_ROOT}/r4"
INSTALL_ROOT="${R4_CLIENT_HOME:-${HOME}/.local/share/r4-client}"
VENV_DIR="${INSTALL_ROOT}/.venv"
BIN_DIR="${R4_CLIENT_BIN_DIR:-${HOME}/.local/bin}"
WRAPPER_PATH="${BIN_DIR}/r4"

if [[ ! -f "${R4_DIR}/pyproject.toml" ]]; then
    echo "error: could not find ${R4_DIR}/pyproject.toml" >&2
    exit 1
fi

find_python() {
    local candidate
    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "${candidate}" >/dev/null 2>&1; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done
    return 1
}

PYTHON_BIN="$(find_python || true)"
if [[ -z "${PYTHON_BIN}" ]]; then
    echo "error: python3.10+ is required but was not found in PATH" >&2
    exit 1
fi

mkdir -p "${INSTALL_ROOT}" "${BIN_DIR}"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    echo "Creating virtual environment at ${VENV_DIR}"
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

R4_CONTRACTS_DIR="${REPO_ROOT}/r4-contracts"
if [[ ! -f "${R4_CONTRACTS_DIR}/pyproject.toml" ]]; then
    echo "error: could not find ${R4_CONTRACTS_DIR}/pyproject.toml" >&2
    exit 1
fi

SCIENCE_DIR="${REPO_ROOT}/science"
if [[ ! -f "${SCIENCE_DIR}/pyproject.toml" ]]; then
    echo "error: could not find ${SCIENCE_DIR}/pyproject.toml" >&2
    exit 1
fi

echo "Installing r4 from ${R4_DIR} (with companion extras)"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install \
    -e "${R4_CONTRACTS_DIR}" \
    -e "${SCIENCE_DIR}" \
    -e "${R4_DIR}[companion]"

cat > "${WRAPPER_PATH}" <<EOF
#!/usr/bin/env bash
exec "${VENV_DIR}/bin/python" -m r4.cli "\$@"
EOF
chmod +x "${WRAPPER_PATH}"

echo
echo "r4 client installation complete."
echo "  CLI wrapper: ${WRAPPER_PATH}"
echo "  Virtualenv:   ${VENV_DIR}"
echo
if [[ ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
    echo "Add ${BIN_DIR} to your PATH before using the wrapper:"
    echo "  export PATH=\"${BIN_DIR}:\$PATH\""
    echo
fi
echo "Next steps:"
echo "  1. export PATH=\"${BIN_DIR}:\$PATH\""
echo "  2. export R4_OIDC_ISSUER=\"https://<your-okta-domain>/oauth2/default\""
echo "  3. export R4_OIDC_CLIENT_ID=\"<public-client-id>\""
echo "  4. r4 config init --gateway-url https://<gateway-host> --project <project-id> --oidc"
echo "  5. r4 health"