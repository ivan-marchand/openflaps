#!/usr/bin/env bash
# Build the OpenFlaps hardware kit and publish it as a GitHub release.
#
#   tools/release.sh <version> [--dry-run]
#
# <version> is a calendar version: YYYY.MM, or YYYY.MM.N for another release in
# the same month. It is used verbatim as the git tag (no "v" prefix).
#
# Builds release/<version>/openflaps-<version>.zip (STLs, flap 3MFs, guide PDFs,
# PCB files, licenses, catalog photos, start-here README) and
# release/<version>/catalog/ (the zip plus loose STL/3MF/photo files to drag into
# Thingiverse, Printables...). Then, after a confirmation, tags the release,
# pushes the tag and creates the GitHub release with the zip attached.
#
# --dry-run builds everything but skips the branch/sync checks and the publish.
#
# PDFs are rendered with markdown-pdf, installed on first run into a private,
# gitignored venv (tools/release/.venv/, needs python3).
set -euo pipefail

GH_REPO="ivan-marchand/openflaps"
MARKDOWN_PDF_VERSION="1.13.3"
# Pinned because newer PyMuPDF releases ship no prebuilt wheel for Intel Macs, and
# building MuPDF from source takes many minutes; --only-binary keeps pip from trying.
PYMUPDF_VERSION="1.28.2"

usage() {
  echo "usage: tools/release.sh <version> [--dry-run]   (version like 2026.11 or 2026.11.1)" >&2
  exit 1
}

VERSION=""
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -*) echo "Error: unknown option $arg" >&2; usage ;;
    *)
      if [ -n "$VERSION" ]; then usage; fi
      VERSION="$arg"
      ;;
  esac
done
[ -n "$VERSION" ] || usage
if ! [[ "$VERSION" =~ ^[0-9]{4}\.[0-9]{2}(\.[0-9]+)?$ ]]; then
  echo "Error: '$VERSION' is not a version like 2026.11 or 2026.11.1" >&2
  exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"
VENV="$REPO_ROOT/tools/release/.venv"
OUT_DIR="release/$VERSION"
ZIP="$OUT_DIR/openflaps-$VERSION.zip"

fail() { echo "Error: $*" >&2; exit 1; }

# --- checks --------------------------------------------------------------
[ -z "$(git status --porcelain)" ] || fail "working tree not clean; commit or stash your changes first"
case "$(git remote get-url origin 2>/dev/null)" in
  *"$GH_REPO"|*"$GH_REPO.git") ;;
  *) fail "origin is not $GH_REPO; releases are published there" ;;
esac

# A tag that already exists is fine only when it marks HEAD and its GitHub release
# doesn't exist yet: an earlier run stopped half-way (push or upload failed), and
# this run finishes the job instead of leaving the version stuck.
HEAD_SHA="$(git rev-parse HEAD)"
LOCAL_TAG="$(git rev-parse -q --verify "refs/tags/$VERSION^{commit}" || true)"
if ! REMOTE_REFS="$(git ls-remote --tags origin "refs/tags/$VERSION" "refs/tags/$VERSION^{}")"; then
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "Warning: could not reach origin to check for tag $VERSION" >&2
    REMOTE_REFS=""
  else
    fail "could not reach origin to check for tag $VERSION"
  fi
fi
# The peeled ref (^{}) is the commit an annotated tag points to; a lightweight tag has none.
REMOTE_TAG="$(printf '%s\n' "$REMOTE_REFS" | awk '/\^\{\}$/ {print $1}')"
[ -n "$REMOTE_TAG" ] || REMOTE_TAG="$(printf '%s\n' "$REMOTE_REFS" | awk 'NF {print $1; exit}')"
if [ -n "$LOCAL_TAG$REMOTE_TAG" ]; then
  for sha in $LOCAL_TAG $REMOTE_TAG; do
    [ "$sha" = "$HEAD_SHA" ] || fail "tag $VERSION already exists on another commit; pick a new version"
  done
  if gh release view "$VERSION" --repo "$GH_REPO" >/dev/null 2>&1; then
    fail "release $VERSION is already published; pick a new version"
  fi
  echo "Tag $VERSION already marks HEAD but has no GitHub release: finishing that release."
fi

if [ "$DRY_RUN" -eq 0 ]; then
  branch="$(git rev-parse --abbrev-ref HEAD)"
  [ "$branch" = "master" ] || fail "releases are made from master (currently on $branch); use --dry-run to try it elsewhere"
  git fetch -q origin master
  [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/master)" ] || fail "master is not in sync with origin/master; pull or push first"
  gh auth status >/dev/null 2>&1 || fail "gh is not logged in; run: gh auth login"
fi

# --- venv with markdown-pdf ---------------------------------------------
ensure_venv() {
  installed=$("$VENV/bin/python" -c "import importlib.metadata as m; print(m.version('markdown-pdf'), m.version('PyMuPDF'))" 2>/dev/null || true)
  if [ "$installed" = "$MARKDOWN_PDF_VERSION $PYMUPDF_VERSION" ]; then
    return
  fi
  command -v python3 >/dev/null 2>&1 || fail "python3 is required to install markdown-pdf"
  echo "Installing markdown-pdf==$MARKDOWN_PDF_VERSION (PyMuPDF==$PYMUPDF_VERSION) into $VENV ..."
  rm -rf "$VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --disable-pip-version-check --only-binary PyMuPDF \
    "markdown-pdf==$MARKDOWN_PDF_VERSION" "PyMuPDF==$PYMUPDF_VERSION"
}
ensure_venv

# --- tests and build ----------------------------------------------------
echo "Running tools/release tests ..."
TEST_LOG="$(mktemp)"
"$VENV/bin/python" -m unittest discover -s tools/release >"$TEST_LOG" 2>&1 \
  || { cat "$TEST_LOG" >&2; rm -f "$TEST_LOG"; fail "tests failed"; }
rm -f "$TEST_LOG"

echo "Building $ZIP ..."
"$VENV/bin/python" tools/release/kit.py "$VERSION" "$OUT_DIR"

echo
echo "Kit:     $REPO_ROOT/$ZIP"
echo "Catalog: $REPO_ROOT/$OUT_DIR/catalog/"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "Dry run: nothing tagged or published."
  exit 0
fi

# --- publish -------------------------------------------------------------
read -r -p "Publish OpenFlaps $VERSION to GitHub (tag + release)? [y/N] " answer
case "$answer" in
  y|Y) ;;
  *) echo "Not published. The build is left in $OUT_DIR."; exit 0 ;;
esac

trap 'echo "Error: publishing stopped part-way. Run tools/release.sh $VERSION again to finish it." >&2' ERR
[ -n "$LOCAL_TAG" ] || git tag -a "$VERSION" -m "OpenFlaps $VERSION"
[ -n "$REMOTE_TAG" ] || git push origin "refs/tags/$VERSION"
gh release create "$VERSION" "$ZIP" --repo "$GH_REPO" --title "OpenFlaps $VERSION" --generate-notes --verify-tag
trap - ERR
echo "Catalog files ready to upload: $REPO_ROOT/$OUT_DIR/catalog/"
