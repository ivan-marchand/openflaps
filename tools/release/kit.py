"""Build the OpenFlaps hardware kit for a release.

Usage: kit.py <version> <out_dir>

Produces <out_dir>/openflaps-<version>.zip (STLs, flap 3MFs, guide PDFs, PCB
files, licenses, catalog photos, a start-here README) and <out_dir>/catalog/
(the zip plus loose STL/3MF/photo files for Thingiverse, Printables...).
Called by tools/release.sh.
"""
import posixpath
import re
import shutil
import sys
import zipfile
from pathlib import Path

REPO_URL = "https://github.com/ivan-marchand/openflaps"

# Guides rendered to PDF: repo path -> PDF file name (the PDFs sit together in guides/).
GUIDES = {
    "mechanical/ASSEMBLY-GUIDE.md": "OpenFlaps-Assembly-Guide.pdf",
    "mechanical/PRINT-GUIDE.md": "OpenFlaps-Print-Guide.pdf",
}

_VERSION_RE = re.compile(r"^[0-9]{4}\.[0-9]{2}(\.[0-9]+)?$")
# A Markdown link that is not an image: [text](target)
_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)\)")
_ABSOLUTE_PREFIXES = ("http:", "https:", "mailto:", "#")


def validate_version(v: str) -> bool:
    """True for a calendar version: YYYY.MM or YYYY.MM.N."""
    return bool(_VERSION_RE.match(v))


def rewrite_links(md, src_dir, version, repo_root, local=None, keep_under=None):
    """Make the relative links of a Markdown file (at repo path src_dir) work outside the repo.

    A link to a path in `local` (repo path -> replacement) becomes that replacement, a link
    under `keep_under` is left alone, and any other relative link points to GitHub at the
    release tag. Images, absolute URLs and in-page anchors are never touched.
    """
    local = local or {}
    repo_root = Path(repo_root)

    def replace(m):
        text, target = m.group(1), m.group(2)
        if target.startswith(_ABSOLUTE_PREFIXES):
            return m.group(0)
        path, _, anchor = target.partition("#")
        resolved = posixpath.normpath(posixpath.join(src_dir, path))
        if resolved in local:
            return f"[{text}]({local[resolved]})"
        if keep_under and (resolved == keep_under or resolved.startswith(keep_under + "/")):
            return m.group(0)
        kind = "tree" if path.endswith("/") or (repo_root / resolved).is_dir() else "blob"
        url = f"{REPO_URL}/{kind}/{version}/{resolved}"
        if anchor:
            url += "#" + anchor
        return f"[{text}]({url})"

    return _LINK_RE.sub(replace, md)


_LICENSE_EDITS = (
    ("this directory and its subdirectories", "the `stl/` folder and its subfolders"),
    ("`flaps/`", "`stl/flaps/`"),
    ("(../../LICENSE)", "(LICENSE)"),
)


def adapt_models_license(text: str) -> str:
    """Reword mechanical/stl/LICENSE.md so it reads correctly at the kit root."""
    for old, new in _LICENSE_EDITS:
        if old not in text:
            raise ValueError(f"mechanical/stl/LICENSE.md no longer contains {old!r}; update kit.py")
        text = text.replace(old, new)
    return text


def render_readme(template: str, version: str) -> str:
    """Fill the start-here README template."""
    return (
        template.replace("{{VERSION}}", version)
        .replace("{{REPO_URL}}", REPO_URL)
        .replace("{{TAG_URL}}", f"{REPO_URL}/tree/{version}")
    )


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _slug(title: str) -> str:
    """GitHub's heading anchor: lowercase, drop punctuation, spaces to hyphens."""
    title = re.sub(r"[`*_\[\]()]", "", title).lower()
    title = re.sub(r"[^\w\- ]", "", title)
    return title.replace(" ", "-")


def add_heading_anchors(md: str) -> str:
    """Put an <a id="slug"> before each heading so in-page links (#slug) resolve in the PDF.

    Uses GitHub's slugs (duplicates get -1, -2...) since the guides are written for GitHub.
    Lines inside ``` code fences are left alone.
    """
    out, seen, in_code = [], {}, False
    for line in md.split("\n"):
        if line.lstrip().startswith("```"):
            in_code = not in_code
        m = None if in_code else _HEADING_RE.match(line)
        if m:
            slug = _slug(m.group(2))
            n = seen.get(slug, 0)
            seen[slug] = n + 1
            out.append(f'<a id="{slug if n == 0 else f"{slug}-{n}"}"></a>\n')
        out.append(line)
    return "\n".join(out)


PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png")
PDF_CSS = (
    "table { border-collapse: collapse; } "
    "td, th { border: 1px solid #999; padding: 2px 4px; } "
    "img { max-width: 100%; } "
    # The build photos sit three to a table row; without a width MuPDF shows them as
    # tiny thumbnails (and width: 100% collapses the table). 140px fits three on A4.
    "td img { width: 140px; }"
)


def render_pdf(md_path, out_pdf, version, repo_root):
    """Render a guide to PDF, with its links rewritten to work outside the repo."""
    import pymupdf  # both installed by tools/release.sh
    from markdown_pdf import MarkdownPdf, Section

    md_path, repo_root = Path(md_path), Path(repo_root)
    src_dir = md_path.parent.relative_to(repo_root).as_posix()
    text = rewrite_links(md_path.read_text(), src_dir, version, repo_root, local=GUIDES)
    text = add_heading_anchors(text)
    title = "Assembly" if "ASSEMBLY" in md_path.name else "Print"
    pdf = MarkdownPdf(toc_level=2)
    pdf.meta["title"] = f"OpenFlaps {title} Guide {version}"
    pdf.add_section(Section(text, root=str(md_path.parent), paper_size="A4"), user_css=PDF_CSS)
    raw = Path(str(out_pdf) + ".raw")
    pdf.save(str(raw))
    # markdown-pdf embeds images uncompressed (the assembly guide came out at ~110 MB);
    # re-encode them as JPEG at print resolution (~4.5 MB).
    doc = pymupdf.open(str(raw))
    doc.rewrite_images(dpi_threshold=200, dpi_target=150, quality=80)
    doc.save(str(out_pdf), garbage=4, deflate=True)
    doc.close()
    raw.unlink()


def _files(folder, suffixes=None):
    """Sorted plain files in folder (not recursive), skipping dotfiles like .DS_Store."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(
        f for f in folder.iterdir()
        if f.is_file() and not f.name.startswith(".")
        and (suffixes is None or f.suffix.lower() in suffixes)
    )


def _copy_files(files, dest):
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(f, dest / f.name)


def _copy_tree(src, dest):
    shutil.copytree(src, dest, ignore=lambda d, names: [n for n in names if n.startswith(".")])


def build_kit(repo_root, version, out_dir, pdf_renderer=render_pdf):
    """Build out_dir/openflaps-<version>.zip and out_dir/catalog/; return the zip path."""
    repo_root, out_dir = Path(repo_root).resolve(), Path(out_dir).resolve()
    # out_dir is deleted first, so never accept the repo, a folder in it that holds
    # source files, or one of its parents (e.g. a mistyped `kit.py 2026.11 .`).
    if (
        out_dir == repo_root
        or repo_root.is_relative_to(out_dir)
        or (out_dir.is_relative_to(repo_root) and not out_dir.is_relative_to(repo_root / "release"))
        or (out_dir / ".git").exists()
    ):
        raise ValueError(f"refusing to use {out_dir} as the output folder: it would delete files")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    name = f"openflaps-{version}"
    stage = out_dir / name
    stage.mkdir(parents=True)

    stl_dir = repo_root / "mechanical/stl"
    stls = _files(stl_dir, (".stl",))
    flaps = _files(stl_dir / "flaps", (".3mf",))
    photos = _files(repo_root / "mechanical/images/catalog", PHOTO_SUFFIXES)

    template = (repo_root / "tools/release/README.md.tmpl").read_text()
    (stage / "README.md").write_text(render_readme(template, version))
    shutil.copy2(repo_root / "LICENSE", stage / "LICENSE")
    (stage / "LICENSE-models.md").write_text(
        adapt_models_license((stl_dir / "LICENSE.md").read_text())
    )

    (stage / "guides").mkdir()
    for guide, pdf_name in GUIDES.items():
        pdf_renderer(repo_root / guide, stage / "guides" / pdf_name, version, repo_root)

    _copy_files(stls, stage / "stl")
    _copy_files(flaps, stage / "stl/flaps")

    _copy_tree(repo_root / "hardware/pcb", stage / "pcb")
    pcb_readme = stage / "pcb/README.md"
    if pcb_readme.exists():
        pcb_readme.write_text(
            rewrite_links(pcb_readme.read_text(), "hardware/pcb", version, repo_root,
                          keep_under="hardware/pcb")
        )

    if photos:
        _copy_files(photos, stage / "photos")
    else:
        print("note: no catalog photos in mechanical/images/catalog/; the kit has no photos/ folder")

    zip_path = out_dir / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(p for p in stage.rglob("*") if p.is_file()):
            zf.write(f, f.relative_to(out_dir).as_posix())

    catalog = out_dir / "catalog"
    catalog.mkdir()
    shutil.copy2(zip_path, catalog / zip_path.name)
    _copy_files(stls, catalog / "stl")
    _copy_files(flaps, catalog / "flaps")
    if photos:
        _copy_files(photos, catalog / "photos")

    shutil.rmtree(stage)
    return zip_path


def main(argv):
    if len(argv) != 2:
        print("usage: kit.py <version> <out_dir>", file=sys.stderr)
        return 2
    version, out_dir = argv
    if not validate_version(version):
        print(f"error: {version!r} is not a version like 2026.11 or 2026.11.1", file=sys.stderr)
        return 2
    repo_root = Path(__file__).resolve().parents[2]
    print(build_kit(repo_root, version, Path(out_dir).resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
