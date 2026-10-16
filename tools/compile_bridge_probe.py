#!/usr/bin/env python3
"""Freeze explicit Unity/Roslyn inputs and compile into a new isolated directory.

This does not run Unity's import pipeline, IL postprocessing, IL2CPP, or package
an APK. Input response files are trusted compiler configuration, but output and
file switches are validated and rewritten before a compiler can be launched.
No original source or dependency is modified. Manifests contain local paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import time
import uuid


OUTPUT_OPTIONS = {"out", "refout", "pdb", "doc", "generatedfilesout"}
INPUT_OPTIONS = {"r", "reference", "analyzer", "additionalfile", "additionalfiles", "sourcelink"}
VALUE_OPTIONS = {
    "target", "t", "langversion", "debug", "runtimemetadataversion", "nowarn",
    "warn", "warnaserror", "nullable", "platform", "checksumalgorithm", "features",
    "subsystemversion", "codepage", "preferreduilang", "pathmap", "main", "moduleassemblyname",
}
FLAG_OPTIONS = {
    "unsafe", "deterministic", "optimize", "nologo", "utf8output", "nostdlib",
    "checked", "publicsign", "fullpaths", "highentropyva", "reportanalyzer", "parallel",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _quote(path: Path) -> str:
    value = str(path)
    if any(character in value for character in ('"', "\n", "\r")):
        raise ValueError("Compiler paths cannot contain quotes or line breaks")
    return '"' + value.replace("\\", "/") + '"'


def _existing(path, base: Path | None = None) -> Path:
    result = Path(str(path).strip('"'))
    if not result.is_absolute() and base is not None:
        result = base / result
    result = result.resolve(strict=True)
    if not result.is_file():
        raise ValueError(f"Expected existing input file: {result}")
    return result


def _single_response_token(line: str) -> None:
    if '\\"' in line:
        raise ValueError("Escaped quotes in input response files are not permitted")
    quoted = False
    for character in line:
        if character == '"':
            quoted = not quoted
        elif character.isspace() and not quoted:
            raise ValueError("Each response-file line must contain one compiler argument")
    if quoted:
        raise ValueError("Unbalanced response-file quotes")


def _parse_response(path: Path, project: Path):
    options, dependencies, defines, outputs = [], [], set(), {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("@"):
            raise ValueError("Nested response files are not permitted")
        _single_response_token(line)
        if line.strip('"').lower().endswith(".cs"):
            # Source membership is regenerated from the explicit current root.
            continue
        match = re.fullmatch(r"[-/]([A-Za-z][A-Za-z0-9]*)([+-]?)(?::(.*))?", line)
        if not match:
            raise ValueError(f"Unsupported response-file entry: {line}")
        name, modifier, value = match.groups()
        name = name.lower()
        if name in {"define", "d"}:
            defines.update(re.split("[;,]", (value or "").strip('"')))
        elif name in OUTPUT_OPTIONS:
            if name in outputs:
                raise ValueError(f"Duplicate compiler output switch: {name}")
            output_value = (value or "").strip('"').replace("\\", "/")
            if not output_value or ".." in PurePosixPath(output_value).parts:
                raise ValueError("Output switch contains an empty or traversal path")
            outputs[name] = Path(output_value).name
        elif name in INPUT_OPTIONS:
            if not value:
                raise ValueError(f"Empty compiler input switch: {name}")
            dependencies.append((name, _existing(value, project)))
        elif name in VALUE_OPTIONS and value is not None:
            options.append(line)
        elif name in FLAG_OPTIONS and value is None:
            options.append(line)
        else:
            raise ValueError(f"Unsupported compiler option (fail closed): {name}")
    if "out" not in outputs or not outputs["out"].lower().endswith(".dll"):
        raise ValueError("A single library DLL -out is required")
    return options, dependencies, set(defines) - {""}, outputs


def compile_probe(*, response_file, project_root, source_root, extra_sources,
                  compiler, runtime, output_dir, define_additions=(),
                  define_removals=(), patches=(), timeout_seconds=300):
    project = Path(project_root).resolve(strict=True)
    root = Path(source_root).resolve(strict=True)
    output = Path(output_dir).resolve()
    response = _existing(response_file)
    compiler = _existing(compiler)
    runtime = _existing(runtime) if runtime else None
    if not project.is_dir() or not root.is_dir() or not root.is_relative_to(project):
        raise ValueError("Source root must be a directory within project root")
    if output.exists() or output.is_relative_to(project) or project.is_relative_to(output):
        raise ValueError("Output must be a new directory outside the source project")
    if any(item.parent != root for item in root.rglob("*.asmdef")):
        raise ValueError("Nested asmdef requires an explicitly scoped source root")
    options, dependencies, defines, outputs = _parse_response(response, project)
    defines.difference_update(define_removals)
    defines.update(define_additions)
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", value) for value in defines):
        raise ValueError("Invalid conditional compilation symbol")
    primary = sorted(root.rglob("*.cs"), key=lambda path: path.as_posix().lower())
    if not primary:
        raise ValueError("Source root contains no C# files")
    source_records, source_bytes = [], {}
    for source in primary:
        resolved = source.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError("Source link escapes its declared root")
        relative = source.relative_to(root).as_posix()
        data = source.read_bytes()
        source_bytes[relative] = data
        source_records.append({"relative_path": relative, "original_sha256": hashlib.sha256(data).hexdigest()})
    patch_records = []
    for patch in patches:
        relative = patch["relative_path"].replace("\\", "/")
        if relative not in source_bytes or PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
            raise ValueError("Patch path is outside the current source membership")
        original = source_bytes[relative]
        if hashlib.sha256(original).hexdigest() != patch["sha256"]:
            raise ValueError("Patch baseline SHA256 differs from the actual source")
        text = original.decode("utf-8-sig")
        if not patch["anchor"] or text.count(patch["anchor"]) != 1:
            raise ValueError("Patch anchor must match exactly once")
        changed = text.replace(patch["anchor"], patch["replacement"], 1).encode("utf-8")
        source_bytes[relative] = changed
        patch_records.append({"relative_path": relative, "before_sha256": patch["sha256"],
                              "after_sha256": hashlib.sha256(changed).hexdigest(),
                              "anchor_sha256": hashlib.sha256(patch["anchor"].encode()).hexdigest()})
    extra_files = []
    for entry in extra_sources:
        path = Path(entry).resolve(strict=True)
        if path.is_dir():
            extra_files.extend(sorted(path.rglob("*.cs")))
        else:
            extra_files.append(_existing(path))
    extra_files = list(dict.fromkeys(extra_files))
    for path in [response, compiler, *extra_files, *(item[1] for item in dependencies)]:
        if path.is_relative_to(output):
            raise ValueError("An input is contained in the output directory")
    for item in source_records:
        item["frozen_sha256"] = hashlib.sha256(source_bytes[item["relative_path"]]).hexdigest()
    extra_data = [(path, path.read_bytes()) for path in extra_files]
    source_snapshot_hash = _digest({"primary": source_records, "extra": [
        {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()} for path, data in extra_data]})
    compiler_hash = sha256_file(compiler)
    tool_hash = sha256_file(Path(__file__).resolve())
    runtime_hash = sha256_file(runtime) if runtime else None
    response_hash = sha256_file(response)
    response_sources = [line.strip().strip('"') for line in response.read_text(encoding="utf-8-sig").splitlines()
                        if line.strip().strip('"').lower().endswith(".cs") and not line.strip().startswith(("-", "/", "@"))]
    # All validation above precedes output creation. Never reuse a previous run.
    output.mkdir(parents=True, exist_ok=False)
    binaries = output / "bin"
    binaries.mkdir()
    scratch = output / "temp"
    scratch.mkdir()
    rewritten = list(options)
    for name, filename in outputs.items():
        destination = output / "generated" if name == "generatedfilesout" else binaries / filename
        if name == "generatedfilesout":
            destination.mkdir()
        rewritten.append(f"-{name}:{_quote(destination)}")
    rewritten.extend(f"-define:{value}" for value in sorted(defines))
    frozen_paths, dependency_records = [], []
    for item in source_records:
        destination = output / "sources" / "primary" / item["relative_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source_bytes[item["relative_path"]])
        frozen_paths.append(destination)
    for index, (path, data) in enumerate(extra_data):
        destination = output / "sources" / "extra" / str(index) / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        frozen_paths.append(destination)
    copied = {}

    def freeze_dependency(path):
        if path in copied:
            return copied[path]
        group = hashlib.sha256(str(path.parent).encode()).hexdigest()[:16]
        destination = output / "dependencies" / group / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        before = sha256_file(path)
        shutil.copyfile(path, destination)
        after = sha256_file(destination)
        if before != after or before != sha256_file(path):
            raise ValueError("Dependency changed while its bytes were frozen")
        copied[path] = destination
        dependency_records.append({"original_path": str(path), "frozen_path": str(destination), "sha256": after})
        return destination

    for name, path in dependencies:
        destination = freeze_dependency(path)
        rewritten.append(f"-{name}:{_quote(destination)}")
        if name == "analyzer":
            for sibling in sorted(path.parent.glob("*.dll")):
                freeze_dependency(sibling.resolve())
    build_id = "inputs:" + _digest({"nonce": uuid.uuid4().hex, "sources": source_snapshot_hash,
        "dependencies": dependency_records, "response": response_hash, "compiler": compiler_hash,
        "runtime": runtime_hash, "tool": tool_hash, "defines": sorted(defines)})
    stamp = output / "sources" / "generated" / "ProviderBuildStamp.cs"
    stamp.parent.mkdir(parents=True)
    stamp.write_text('#if UNITY_EDITOR || (DEVELOPMENT_BUILD && AI_TEST_AGENT_BRIDGE)\n'
        'namespace AiTestAgent.Generated { internal static class ProviderBuildStamp { '
        f'public const string Value = "{build_id}"; }} }}\n#endif\n', encoding="utf-8")
    rewritten.extend(_quote(path) for path in [*frozen_paths, stamp])
    frozen_response = output / "compile.rsp"
    frozen_response.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    command = [str(compiler), "@" + str(frozen_response)]
    if runtime:
        command.insert(0, str(runtime))
    manifest = {"schema_version": 1, "status": "prepared", "evidence_scope": "isolated_roslyn_source_compile",
        "not_executed": ["Unity_import", "IL_postprocessing", "IL2CPP", "APK_build", "Android_execution"],
        "runtime_build_id": build_id, "source_snapshot_sha256": source_snapshot_hash,
        "project_root": str(project), "source_root": str(root), "tool_sha256": tool_hash,
        "original_response_source_count": len(response_sources),
        "missing_original_response_source_count": sum(not (project / path).is_file() for path in response_sources),
        "response_file": str(response), "response_sha256": response_hash,
        "frozen_response_file": str(frozen_response), "frozen_response_sha256": sha256_file(frozen_response),
        "compiler": str(compiler), "compiler_sha256": compiler_hash, "runtime": str(runtime) if runtime else None,
        "runtime_sha256": runtime_hash, "command": command, "defines": sorted(defines),
        "primary_source_count": len(primary), "extra_source_count": len(extra_data),
        "sources": source_records, "extra_sources": [{"original_path": str(path), "sha256": hashlib.sha256(data).hexdigest()} for path, data in extra_data],
        "dependencies": dependency_records, "patches": patch_records, "output_dir": str(output)}
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    environment = os.environ.copy()
    environment.update({"TMP": str(scratch), "TEMP": str(scratch), "TMPDIR": str(scratch), "GIT_OPTIONAL_LOCKS": "0"})
    started = time.monotonic()
    with (output / "compiler.log").open("wb") as log:
        result = subprocess.run(command, cwd=output, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                timeout=timeout_seconds, check=False)
    manifest["elapsed_seconds"] = round(time.monotonic() - started, 3)
    manifest["compile_exit_code"] = result.returncode
    dll = binaries / outputs["out"]
    manifest["status"] = "compiled" if result.returncode == 0 and dll.is_file() else "compile_failed"
    manifest["dll_path"] = str(dll)
    manifest["dll_sha256"] = sha256_file(dll) if dll.is_file() else None
    if compiler_hash != sha256_file(compiler) or (runtime and runtime_hash != sha256_file(runtime)):
        manifest["status"] = "compiler_changed_during_compile"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("response-file", "project-root", "source-root", "compiler", "output-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--extra-source", action="append", default=[], type=Path)
    parser.add_argument("--define-add", action="append", default=[])
    parser.add_argument("--define-remove", action="append", default=[])
    parser.add_argument("--patch-file", type=Path, help="JSON list: relative_path, sha256, anchor, replacement")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    result = compile_probe(response_file=args.response_file, project_root=args.project_root,
        source_root=args.source_root, extra_sources=args.extra_source, compiler=args.compiler, runtime=args.runtime,
        output_dir=args.output_dir, define_additions=args.define_add, define_removals=args.define_remove,
        patches=json.loads(args.patch_file.read_text(encoding="utf-8")) if args.patch_file else (),
        timeout_seconds=args.timeout_seconds)
    print(json.dumps({key: result[key] for key in ("status", "runtime_build_id", "source_snapshot_sha256", "dll_path", "dll_sha256")}))
    return 0 if result["status"] == "compiled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
