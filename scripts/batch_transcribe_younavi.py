#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch-transcribe local audio through the installed YouNavi client."""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from compat import ensure_dir, expand_path


AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".amr"}
DEFAULT_AGENT = "/Applications/YouNavi.app/Contents/Resources/backend/agent-cli"


def audio_files(source):
    source = expand_path(source)
    if source.is_file():
        return [source] if source.suffix.lower() in AUDIO_EXTS else []
    return sorted(p for p in source.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS)


def main():
    parser = argparse.ArgumentParser(description="Batch transcribe audio with YouNavi.")
    parser.add_argument("input", help="audio file or directory")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--agent", default=DEFAULT_AGENT)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    if not Path(args.agent).exists():
        print("ERROR: YouNavi agent-cli not found: {0}".format(args.agent), file=sys.stderr)
        return 2

    source = expand_path(args.input)
    output = expand_path(args.output_dir)
    ensure_dir(output)
    manifest = output / "transcript_manifest.jsonl"
    rows = []
    for audio in audio_files(source):
        rel = audio.name if source.is_file() else str(audio.relative_to(source))
        target = output / Path(rel).with_suffix(".txt")
        ensure_dir(target.parent)
        if target.exists() and target.stat().st_size > 0:
            rows.append({"source": str(audio), "transcript": str(target), "status": "skipped_existing"})
            continue
        try:
            proc = subprocess.Popen([args.agent, "-f", "json", "audio", "transcribe", str(audio)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = proc.communicate()
            stdout = stdout.decode("utf-8", "replace")
            stderr = stderr.decode("utf-8", "replace")
            payload = json.loads(stdout) if stdout.strip() else {}
            text = ((payload.get("data") or {}).get("text") or "").strip()
            if proc.returncode != 0 or not text:
                error = payload.get("error") or payload.get("message") or stderr.strip() or "empty transcript"
                raise RuntimeError(str(error))
            with io.open(str(target), "w", encoding="utf-8") as handle:
                handle.write(text + "\n")
            rows.append({"source": str(audio), "transcript": str(target), "status": "ok",
                         "output_path": (payload.get("data") or {}).get("output_path", "")})
            print("TRANSCRIBED {0}".format(rel))
        except Exception as exc:
            rows.append({"source": str(audio), "status": "failed", "error": str(exc)})
            print("FAILED {0}: {1}".format(rel, exc), file=sys.stderr)

    with io.open(str(manifest), "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    ok = sum(1 for row in rows if row["status"] == "ok")
    failed = sum(1 for row in rows if row["status"] == "failed")
    print(json.dumps({"total": len(rows), "ok": ok, "failed": failed,
                      "manifest": str(manifest)}, ensure_ascii=False))
    return 1 if failed and not ok else 0


if __name__ == "__main__":
    sys.exit(main())
