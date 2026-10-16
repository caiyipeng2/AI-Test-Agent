"""Safety tests for isolated compilation, using an inert response-file consumer."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import shutil
import uuid
import unittest


TOOL = Path(__file__).resolve().parents[1] / "tools" / "compile_bridge_probe.py"


class CompileBridgeProbeTests(unittest.TestCase):
    def setUp(self):
        scratch = TOOL.parent.parent / ".local" / "m32-compile-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.root = scratch / uuid.uuid4().hex
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.project = self.root / "game"
        self.sources = self.project / "Assets" / "Hotfix"
        self.sources.mkdir(parents=True)
        (self.sources / "Current.cs").write_text("class Current {}", encoding="utf-8")
        (self.project / "Lib").mkdir()
        (self.project / "Lib" / "Dep.dll").write_bytes(b"dependency")
        self.extra = self.root / "adapter.cs"
        self.extra.write_text("class Adapter {}", encoding="utf-8")
        self.compiler = self.root / "fake_compiler.py"
        self.compiler.write_text(
            "import pathlib,sys\n"
            "p=pathlib.Path(sys.argv[1][1:]); lines=p.read_text().splitlines()\n"
            "for line in lines:\n"
            " if line.startswith(('-out:', '-refout:', '-pdb:', '-doc:')):\n"
            "  q=pathlib.Path(line.split(':',1)[1].strip(chr(34))); q.write_bytes(b'fresh fake output')\n",
            encoding="utf-8",
        )
        self.rsp = self.project / "game.rsp"
        self.rsp.write_text(
            '-target:library\n-out:"Library/game.dll"\n-refout:"Library/game.ref.dll"\n'
            '-pdb:"Library/game.pdb"\n-doc:"Library/game.xml"\n'
            '-r:"Lib/Dep.dll"\n-define:UNITY_EDITOR;OLD\n/utf8output\n'
            '"Assets/Hotfix/Deleted.cs"\n', encoding="utf-8"
        )

    def load_tool(self):
        self.assertTrue(TOOL.is_file(), "isolated compiler tool must exist")
        spec = importlib.util.spec_from_file_location("compile_bridge_probe", TOOL)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def run_probe(self, module, output=None, **kwargs):
        return module.compile_probe(
            response_file=self.rsp, project_root=self.project,
            source_root=self.sources, extra_sources=[self.extra],
            compiler=self.compiler, runtime=Path(sys.executable),
            output_dir=output or self.root / "isolated",
            define_additions=["NEW"], define_removals=["OLD"], **kwargs,
        )

    def test_regenerates_sources_freezes_inputs_and_redirects_all_outputs(self):
        module = self.load_tool()
        manifest = self.run_probe(module)
        output = self.root / "isolated"
        self.assertEqual(manifest["compile_exit_code"], 0)
        self.assertEqual(manifest["primary_source_count"], 1)
        self.assertEqual(manifest["source_root"], str(self.sources.resolve()))
        self.assertEqual(manifest["original_response_source_count"], 1)
        self.assertEqual(manifest["missing_original_response_source_count"], 1)
        self.assertEqual(len(manifest["tool_sha256"]), 64)
        self.assertTrue(manifest["runtime_build_id"].startswith("inputs:"))
        frozen_rsp = (output / "compile.rsp").read_text()
        self.assertNotIn("Deleted.cs", frozen_rsp)
        self.assertNotIn("OLD", frozen_rsp)
        self.assertIn("NEW", frozen_rsp)
        self.assertNotIn(str(self.sources), frozen_rsp)
        self.assertNotIn(str(self.project / "Lib"), frozen_rsp)
        for option in ("out", "refout", "pdb", "doc"):
            line = next(line for line in frozen_rsp.splitlines() if line.startswith(f"-{option}:"))
            self.assertTrue(Path(line.split(":", 1)[1].strip('"')).is_relative_to(output))
        self.assertFalse((self.project / "Library").exists())
        self.assertEqual(json.loads((output / "manifest.json").read_text())["status"], "compiled")
        self.assertEqual(len(manifest["dll_sha256"]), 64)

    def test_refuses_existing_or_overlapping_output_before_any_write(self):
        module = self.load_tool()
        sentinel = self.sources / "sentinel.txt"
        sentinel.write_text("keep")
        for output in (self.sources, self.project, self.sources / "nested", self.root):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.run_probe(module, output=output)
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertFalse((self.sources / "nested").exists())

    def test_refuses_nested_response_files_and_unsupported_output_switches(self):
        module = self.load_tool()
        for dangerous in ('@other.rsp', '-touchedfiles:../../escape', '-errorlog:../../escape.json', '-out:../../escape.dll'):
            with self.subTest(dangerous=dangerous):
                original = self.rsp.read_text()
                self.rsp.write_text(original + dangerous + "\n")
                with self.assertRaises(ValueError):
                    self.run_probe(module)
                self.rsp.write_text(original)
                self.assertFalse((self.root / "isolated").exists())

    def test_refuses_additional_switch_hidden_in_a_value_option(self):
        module = self.load_tool()
        original = self.rsp.read_text()
        for option in ('-langversion:9.0 -out:../../escape.dll',
                       '-pathmap:\\"x -errorlog:../../escape.json\\"'):
            with self.subTest(option=option):
                self.rsp.write_text(original + option + '\n')
                with self.assertRaises(ValueError):
                    self.run_probe(module)
                self.assertFalse((self.root / "isolated").exists())

    def test_exact_hash_anchored_patch_changes_only_frozen_copy(self):
        module = self.load_tool()
        source = self.sources / "Current.cs"
        original = source.read_bytes()
        import hashlib
        patch = {"relative_path": "Current.cs", "sha256": hashlib.sha256(original).hexdigest(),
                 "anchor": "class Current {}", "replacement": "class Patched {}"}
        result = self.run_probe(module, patches=[patch])
        self.assertEqual(source.read_bytes(), original)
        self.assertIn("class Patched {}", (self.root / "isolated" / "sources" / "primary" / "Current.cs").read_text())
        self.assertEqual(result["patches"][0]["before_sha256"], patch["sha256"])

    def test_bad_patch_hash_and_source_traversal_are_rejected_before_writes(self):
        module = self.load_tool()
        for patch in ({"relative_path": "../escape.cs", "sha256": "0" * 64, "anchor": "x", "replacement": "y"},
                      {"relative_path": "Current.cs", "sha256": "0" * 64, "anchor": "class Current {}", "replacement": "y"}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.run_probe(module, patches=[patch])
            self.assertFalse((self.root / "isolated").exists())


if __name__ == "__main__":
    unittest.main()
