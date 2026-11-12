"""Host tests for tools/release/kit.py.

Run: python3 -m unittest discover -s tools/release -v
"""
import sys
import tempfile
import zipfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


class HelperTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "hardware/pcb/controller").mkdir(parents=True)
        (self.root / "firmware/attiny84").mkdir(parents=True)
        (self.root / "firmware/attiny84/INSTALL-attiny84.md").write_text("x")
        (self.root / "mechanical/stl").mkdir(parents=True)
        (self.root / "mechanical/stl/LICENSE.md").write_text("x")

    def tearDown(self):
        self._tmp.cleanup()

    def test_validate_version(self):
        for ok in ("2026.11", "2026.11.1", "2026.01.12"):
            self.assertTrue(kit.validate_version(ok), ok)
        for bad in ("v2026.11", "2026.1", "26.11", "2026.11.", "2026-11", ""):
            self.assertFalse(kit.validate_version(bad), bad)

    def test_rewrites_file_link_to_blob(self):
        md = "see [INSTALL](../firmware/attiny84/INSTALL-attiny84.md)."
        out = kit.rewrite_links(md, "mechanical", "2026.11", self.root)
        self.assertEqual(
            out,
            "see [INSTALL](https://github.com/ivan-marchand/openflaps/blob/2026.11/"
            "firmware/attiny84/INSTALL-attiny84.md).",
        )

    def test_rewrites_dir_link_to_tree(self):
        md = "[`hardware/pcb/controller/`](../hardware/pcb/controller/)"
        out = kit.rewrite_links(md, "mechanical", "2026.11", self.root)
        self.assertIn(
            "(https://github.com/ivan-marchand/openflaps/tree/2026.11/hardware/pcb/controller)",
            out,
        )

    def test_keeps_anchor_on_github_link(self):
        out = kit.rewrite_links("[x](stl/LICENSE.md#top)", "mechanical", "2026.11", self.root)
        self.assertIn("/blob/2026.11/mechanical/stl/LICENSE.md#top)", out)

    def test_local_map_wins_and_drops_anchor(self):
        out = kit.rewrite_links(
            "[P](PRINT-GUIDE.md#parts-list)", "mechanical", "2026.11", self.root, local=kit.GUIDES
        )
        self.assertEqual(out, "[P](OpenFlaps-Print-Guide.pdf)")

    def test_leaves_images_absolute_and_anchor_links(self):
        md = (
            "| ![a](images/photos/x.jpg) | ![b](images/y.png) |\n"
            "[web](https://example.com/a) [mail](mailto:a@b.c) [here](#parts-list)"
        )
        self.assertEqual(kit.rewrite_links(md, "mechanical", "2026.11", self.root), md)

    def test_keep_under_leaves_inside_links(self):
        md = "[c](controller/) [i](../../firmware/attiny84/INSTALL-attiny84.md)"
        out = kit.rewrite_links(md, "hardware/pcb", "2026.11", self.root, keep_under="hardware/pcb")
        self.assertIn("[c](controller/)", out)
        self.assertIn("/blob/2026.11/firmware/attiny84/INSTALL-attiny84.md)", out)

    def test_adapt_models_license_on_real_file(self):
        text = (REPO / "mechanical/stl/LICENSE.md").read_text()
        out = kit.adapt_models_license(text)
        self.assertIn("the `stl/` folder and its subfolders", out)
        self.assertIn("`stl/flaps/`", out)
        self.assertIn("](LICENSE)", out)
        self.assertNotIn("../../LICENSE", out)

    def test_adapt_models_license_rejects_reworded(self):
        with self.assertRaises(ValueError):
            kit.adapt_models_license("some other license text")

    def test_heading_anchors_github_slugs(self):
        md = "# Part 1 — Assemble one character\n\ntext\n## Step 1: Print the flaps!\n"
        out = kit.add_heading_anchors(md)
        self.assertIn('<a id="part-1--assemble-one-character"></a>\n\n# Part 1 — Assemble one character', out)
        self.assertIn('<a id="step-1-print-the-flaps"></a>\n\n## Step 1: Print the flaps!', out)

    def test_heading_anchors_skip_code_and_number_duplicates(self):
        md = "## Notes\n```bash\n# not a heading\n```\n## Notes\n"
        out = kit.add_heading_anchors(md)
        self.assertIn("```bash\n# not a heading\n```", out)
        self.assertNotIn('id="not-a-heading"', out)
        self.assertIn('<a id="notes"></a>', out)
        self.assertIn('<a id="notes-1"></a>', out)

    def test_render_readme(self):
        out = kit.render_readme("v{{VERSION}} {{REPO_URL}} {{TAG_URL}}", "2026.11")
        self.assertEqual(
            out,
            "v2026.11 https://github.com/ivan-marchand/openflaps "
            "https://github.com/ivan-marchand/openflaps/tree/2026.11",
        )


def fake_pdf(md_path, out_pdf, version, repo_root):
    Path(out_pdf).write_bytes(b"%PDF fake")


try:
    import markdown_pdf  # noqa: F401

    HAVE_MARKDOWN_PDF = True
except ImportError:
    HAVE_MARKDOWN_PDF = False


class BuildKitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.repo = Path(self.tmp) / "repo"
        self.out = Path(self.tmp) / "release" / "2026.11"
        r = self.repo
        (r / "mechanical/stl/flaps").mkdir(parents=True)
        (r / "hardware/pcb/controller").mkdir(parents=True)
        (r / "firmware/attiny84").mkdir(parents=True)
        (r / "tools/release").mkdir(parents=True)
        (r / "LICENSE").write_text("MIT")
        (r / "mechanical/stl/LICENSE.md").write_text((REPO / "mechanical/stl/LICENSE.md").read_text())
        (r / "mechanical/ASSEMBLY-GUIDE.md").write_text("# A")
        (r / "mechanical/PRINT-GUIDE.md").write_text("# P")
        (r / "mechanical/stl/Split Flap Character - Frame.stl").write_bytes(b"solid")
        (r / "mechanical/stl/.DS_Store").write_bytes(b"junk")
        (r / "mechanical/stl/flaps/flap_1_a_b.3mf").write_bytes(b"3mf")
        (r / "firmware/attiny84/INSTALL-attiny84.md").write_text("x")
        (r / "hardware/pcb/README.md").write_text(
            "[c](controller/) [i](../../firmware/attiny84/INSTALL-attiny84.md)"
        )
        (r / "hardware/pcb/controller/Gerber_PCB_Controller.zip").write_bytes(b"zip")
        (r / "tools/release/README.md.tmpl").write_text(
            (REPO / "tools/release/README.md.tmpl").read_text()
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_zip_layout_without_photos(self):
        z = kit.build_kit(self.repo, "2026.11", self.out, pdf_renderer=fake_pdf)
        self.assertEqual(z, (self.out / "openflaps-2026.11.zip").resolve())
        with zipfile.ZipFile(z) as zf:
            names = set(zf.namelist())
            infos = zf.infolist()
        p = "openflaps-2026.11/"
        for n in (
            "README.md", "LICENSE", "LICENSE-models.md",
            "guides/OpenFlaps-Assembly-Guide.pdf", "guides/OpenFlaps-Print-Guide.pdf",
            "stl/Split Flap Character - Frame.stl", "stl/flaps/flap_1_a_b.3mf",
            "pcb/README.md", "pcb/controller/Gerber_PCB_Controller.zip",
        ):
            self.assertIn(p + n, names)
        self.assertFalse(any(".DS_Store" in n for n in names))
        self.assertFalse(any(n.startswith(p + "photos/") for n in names))
        self.assertTrue(all(i.compress_type == zipfile.ZIP_DEFLATED for i in infos))

    def test_photos_included_when_present(self):
        (self.repo / "mechanical/images/catalog").mkdir(parents=True)
        (self.repo / "mechanical/images/catalog/hero.jpg").write_bytes(b"jpg")
        z = kit.build_kit(self.repo, "2026.11", self.out, pdf_renderer=fake_pdf)
        with zipfile.ZipFile(z) as zf:
            self.assertIn("openflaps-2026.11/photos/hero.jpg", zf.namelist())
        self.assertTrue((self.out / "catalog/photos/hero.jpg").exists())

    def test_catalog_folder(self):
        kit.build_kit(self.repo, "2026.11", self.out, pdf_renderer=fake_pdf)
        c = self.out / "catalog"
        self.assertTrue((c / "openflaps-2026.11.zip").exists())
        self.assertTrue((c / "stl/Split Flap Character - Frame.stl").exists())
        self.assertTrue((c / "flaps/flap_1_a_b.3mf").exists())
        self.assertFalse((c / "stl/.DS_Store").exists())
        self.assertFalse((self.out / "openflaps-2026.11").exists())  # staging removed

    def test_pcb_readme_link_rewritten(self):
        z = kit.build_kit(self.repo, "2026.11", self.out, pdf_renderer=fake_pdf)
        with zipfile.ZipFile(z) as zf:
            text = zf.read("openflaps-2026.11/pcb/README.md").decode()
        self.assertIn("[c](controller/)", text)
        self.assertIn("/blob/2026.11/firmware/attiny84/INSTALL-attiny84.md", text)

    def test_rebuild_removes_stale_files(self):
        self.out.mkdir(parents=True)
        (self.out / "stale.txt").write_text("x")
        kit.build_kit(self.repo, "2026.11", self.out, pdf_renderer=fake_pdf)
        self.assertFalse((self.out / "stale.txt").exists())

    def test_refuses_to_wipe_repo_or_its_parents(self):
        (self.repo / ".git").mkdir()
        for target in (self.repo, self.repo.parent, self.repo / "mechanical"):
            with self.assertRaises(ValueError, msg=str(target)):
                kit.build_kit(self.repo, "2026.11", target, pdf_renderer=fake_pdf)
        self.assertTrue((self.repo / "LICENSE").exists())
        self.assertTrue((self.repo / "mechanical/stl/flaps/flap_1_a_b.3mf").exists())

    def test_main_rejects_bad_version(self):
        self.assertEqual(kit.main(["v2026.11", str(self.out)]), 2)

    @unittest.skipUnless(HAVE_MARKDOWN_PDF, "markdown-pdf not installed (tools/release.sh installs it)")
    def test_render_pdf_real_guide(self):
        out = Path(self.tmp) / "print.pdf"
        kit.render_pdf(REPO / "mechanical/PRINT-GUIDE.md", out, "2026.11", REPO)
        self.assertTrue(out.read_bytes().startswith(b"%PDF"))
        self.assertFalse(Path(str(out) + ".raw").exists())

    @unittest.skipUnless(HAVE_MARKDOWN_PDF, "markdown-pdf not installed (tools/release.sh installs it)")
    def test_render_pdf_assembly_guide_is_small(self):
        # Its in-page links need heading anchors, and its images must be re-encoded.
        out = Path(self.tmp) / "assembly.pdf"
        kit.render_pdf(REPO / "mechanical/ASSEMBLY-GUIDE.md", out, "2026.11", REPO)
        self.assertLess(out.stat().st_size, 15_000_000)


if __name__ == "__main__":
    unittest.main()
