#!/usr/bin/env python3
"""Run the existing Neuronex classifier over a folder without touching LoRa."""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--classifier", type=Path, default=Path("classifier.py"))
    parser.add_argument("--output", type=Path, default=Path("classification_results.jsonl"))
    args = parser.parse_args()
    if not args.folder.is_dir():
        parser.error(f"Folder does not exist: {args.folder}")
    if not args.classifier.is_file():
        parser.error(f"Classifier does not exist: {args.classifier}")
    images = sorted(p for p in args.folder.rglob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    if not images:
        parser.error("No images found")
    decoder = json.JSONDecoder()
    with args.output.open("w", encoding="utf-8") as output:
        for index, image in enumerate(images, 1):
            result = subprocess.run([sys.executable, str(args.classifier), str(image)], check=True,
                                    capture_output=True, text=True, timeout=180)
            parsed = None
            for position, character in enumerate(result.stdout):
                if character == "{":
                    try:
                        candidate, _ = decoder.raw_decode(result.stdout[position:])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(candidate, dict) and ("class" in candidate or "predicted_class" in candidate):
                        parsed = candidate
            if parsed is None:
                raise RuntimeError(f"No JSON classification for {image}: {result.stdout}")
            record = {"file": str(image), "class": parsed.get("class", parsed.get("predicted_class")),
                      "confidence": parsed.get("confidence"), "probs": parsed.get("probs", {})}
            output.write(json.dumps(record, allow_nan=False) + "\n")
            print(f"[{index}/{len(images)}] {image.name} -> {record['class']} ({record['confidence']:.1%})", flush=True)
    print(f"Saved {len(images)} results to {args.output}")


if __name__ == "__main__":
    main()
