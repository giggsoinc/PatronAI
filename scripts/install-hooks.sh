#!/usr/bin/env bash
# =============================================================================
# scripts/install-hooks.sh — Install PatronAI git hooks from scripts/hooks/
# Author: Giggso Inc / Ravi Venugopal
# Purpose: One-time setup; symlinks scripts/hooks/* into .git/hooks/
# =============================================================================
# | Date       | Author | Change                         |
# |------------|--------|--------------------------------|
# | 2026-05-08 | RV     | Initial implementation         |
# =============================================================================

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
SRC="$REPO_ROOT/scripts/hooks"
DST="$REPO_ROOT/.git/hooks"

install_hook() {
    local name="$1"
    if [ ! -f "$SRC/$name" ]; then
        echo "  ⚠ $name not found in scripts/hooks/ — skipping"
        return
    fi
    cp "$SRC/$name" "$DST/$name"
    chmod +x "$DST/$name"
    echo "  ✓ Installed $name"
}

echo ""
echo "Installing PatronAI git hooks..."
echo ""
install_hook "pre-push"
echo ""
echo "Done. Every 'git push' will now run:"
echo "  [1/3] Code quality check"
echo "  [2/3] Structural model check"
echo "  [3/3] GPT-5.5 vulnerability scan"
echo "  [async] PR review → GitHub comments"
echo ""
echo "Requires OPENAI_API_KEY in .env and 'gh' CLI authenticated."
echo ""
