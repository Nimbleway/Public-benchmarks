#!/usr/bin/env bash
set -euo pipefail

# nimble-benchmark first-time setup
# Usage: ./setup.sh

echo "=== nimble-benchmark setup ==="

command -v uv >/dev/null 2>&1 || {
  echo "Error: uv is required. Install it from https://docs.astral.sh/uv/ and rerun ./setup.sh"
  exit 1
}

if command -v python3 >/dev/null 2>&1; then
  python_version="$(python3 - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)"
  case "${python_version}" in
    3.12|3.13) ;;
    *)
      echo "Warning: detected Python ${python_version}; this project requires Python >=3.12,<3.14."
      echo "uv can install the required Python version if it is not already available."
      ;;
  esac
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Edit it with your API keys before running benchmarks."
fi

echo "Installing dependencies..."
uv sync --extra dev

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env with NIMBLE_API_KEY, NIMBLE_BASE_URL, and OPENAI_API_KEY"
echo "  2. Run: make test"
echo "  3. Run: make eval-quick"
echo "  4. Using Claude Code? CLAUDE.md has project context and commands."
