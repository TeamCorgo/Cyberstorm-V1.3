import argparse
import json
import shutil
from pathlib import Path
import sys


def load_patches() -> list[dict]:
    """Load patches.json (next to the exe/script, else the bundled copy).

    Byte fields ("original", "modified") are hex strings such as
    "01 00 00 00 0A"; they are converted to bytes here.
    """
    candidates = [Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent / "patches.json"]

    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "patches.json")

    for path in candidates:
        if path.is_file():
            break
    else:
        raise FileNotFoundError("patches.json not found")

    def to_bytes(node):
        if isinstance(node, dict):
            return {
                k: bytes.fromhex(v) if k in ("original", "modified") else to_bytes(v)
                for k, v in node.items()
            }
        if isinstance(node, list):
            return [to_bytes(v) for v in node]
        return node

    return to_bytes(json.loads(path.read_text(encoding="utf-8")))["patches"]


PATCHES = load_patches()


def find_matches(data: bytes, pattern: bytes) -> list[int]:
    """Return every offset where pattern occurs in data."""
    matches = []
    start = 0

    while True:
        offset = data.find(pattern, start)

        if offset == -1:
            break

        matches.append(offset)
        start = offset + 1

    return matches


def patch_steps(patch: dict) -> list[dict]:
    """A patch is either one edit (original/modified) or a list of steps."""
    return patch.get("steps") or [patch]


def hex_bytes(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def describe_mismatch(data: bytes, original: bytes, modified: bytes) -> str:
    """Explain why `original` was not found: what is in the file instead."""
    already = find_matches(data, modified) if modified != original else []

    if already:
        where = ", ".join(f"0x{o:X}" for o in already[:3])
        return f"the patched bytes are already present @ {where}"

    size = len(original)
    candidates = []

    # Anchor on the longest prefix / suffix of the pattern that does exist.
    for length in range(size - 1, 2, -1):
        for start in find_matches(data, original[:length])[:5]:
            candidates.append(start)

        for hit in find_matches(data, original[size - length:])[:5]:
            candidates.append(hit - (size - length))

        if candidates:
            break

    best_start, best_score = None, 0

    for start in candidates:
        window = data[max(start, 0):start + size]

        if start < 0 or len(window) != size:
            continue

        score = sum(1 for a, b in zip(window, original) if a == b)

        if score > best_score:
            best_start, best_score = start, score

    if best_start is None:
        return "no similar bytes found anywhere in the file"

    found = data[best_start:best_start + size]
    marker = " ".join(
        "  " if a == b else "^^" for a, b in zip(found, original)
    )

    return (
        f"closest match @ 0x{best_start:X} ({best_score}/{size} bytes agree)\n"
        f"      expected: {hex_bytes(original)}\n"
        f"      found:    {hex_bytes(found)}\n"
        f"                {marker}"
    )


def apply_step(data: bytearray, label: str, step: dict) -> int:
    # The primary original comes first; each optional fallback is tried, in
    # order, only when the previous candidates are not present in the file.
    # A fallback may give its own "modified"; otherwise the step's is used.
    candidates = [(step["original"], step["modified"])]

    for fallback in step.get("fallbacks", []):
        candidates.append(
            (fallback["original"], fallback.get("modified", step["modified"]))
        )

    for original, modified in candidates:
        if len(original) != len(modified):
            raise ValueError(
                f"{label}: original and modified byte lengths differ."
            )

    for original, modified in candidates:
        matches = find_matches(data, original)

        if len(matches) > 1:
            offsets = ", ".join(f"0x{o:X}" for o in matches[:8])
            raise ValueError(
                f"{label}: expected exactly 1 match, found {len(matches)} "
                f"@ {offsets}."
            )

        if matches:
            break
    else:
        primary_original, primary_modified = candidates[0]
        tried = (
            f" (also tried {len(candidates) - 1} fallback"
            f"{'' if len(candidates) == 2 else 's'})"
            if len(candidates) > 1
            else ""
        )

        raise ValueError(
            f"{label}: expected bytes not found{tried}.\n"
            f"    {describe_mismatch(bytes(data), primary_original, primary_modified)}"
        )

    offset = matches[0]

    data[offset:offset + len(modified)] = modified

    # Verify modification.
    if data[offset:offset + len(modified)] != modified:
        raise ValueError(f"{label}: verification failed.")

    return offset


def apply_patch(data: bytearray, patch: dict) -> list[int]:
    """Apply every step of a patch; return the offset of each step."""
    steps = patch_steps(patch)
    offsets = []

    for number, step in enumerate(steps, start=1):
        label = f'"{patch["name"]}"'

        if len(steps) > 1:
            note = f': {step["note"]}' if "note" in step else ""
            label += f" (step {number}/{len(steps)}{note})"

        offsets.append(apply_step(data, label, step))

    return offsets


def find_file(folder: Path, name: str) -> Path | None:
    """Find a file by name (any case) in folder, else in its subfolders."""
    for candidates in (folder.iterdir(), folder.rglob("*")):
        for path in sorted(candidates):
            if path.is_file() and path.name.upper() == name.upper():
                return path

    return None


def backup_original(exe_path: Path) -> Path:
    """Copy the exe into an 'original' folder beside it (never overwrites)."""
    backup_dir = exe_path.parent / "original"
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / exe_path.name

    if not backup_path.exists():
        shutil.copy2(exe_path, backup_path)

    return backup_path


def patch_file(source_path: Path, patches: list) -> tuple[bytearray, list]:
    """Apply patches to the bytes of source_path; nothing is written."""
    data = bytearray(source_path.read_bytes())
    results = []

    for patch in patches:
        offsets = apply_patch(data, patch)

        results.append(
            f'{source_path.name}: steps({len(offsets)}): {patch["name"]}'
        )

    return data, results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cyberstrom v1.3 offline EXE patcher."
    )

    parser.add_argument(
        "folder",
        type=Path,
        help="Game folder containing the files to patch",
    )

    args = parser.parse_args()

    folder = args.folder

    if not folder.is_dir():
        print(f"Error: folder does not exist: {folder}", file=sys.stderr)
        return 1

    # Group patches by the file they modify, preserving order.
    by_file: dict[str, list] = {}

    for patch in PATCHES:
        by_file.setdefault(patch["file"], []).append(patch)

    print("Files to modify:")
    for name, patches in by_file.items():
        noun = "patch" if len(patches) == 1 else "patches"
        print(f"  {name} ({len(patches)} {noun})")
    print()

    jobs = []

    for name, patches in by_file.items():
        game_path = find_file(folder, name)

        if game_path is None:
            print(f"Error: {name} not found in {folder}", file=sys.stderr)
            return 1

        print(f"Found: {game_path}")
        jobs.append((game_path, patches))

    # Back up first, then patch from the pristine backup so re-running the
    # patcher works even when the game file is already patched.
    patched = []
    all_results = []

    for game_path, patches in jobs:
        backup_path = backup_original(game_path)
        print(f"Backup: {backup_path}")

        try:
            data, results = patch_file(backup_path, patches)
        except Exception as exc:
            print("Patch aborted. No files were modified.", file=sys.stderr)
            print(file=sys.stderr)
            print(f"{game_path.name}: {exc}", file=sys.stderr)
            return 1

        patched.append((game_path, data))
        all_results += results

    # Every file patched cleanly in memory; now replace the game files.
    for game_path, data in patched:
        game_path.write_bytes(data)
        print(f"Replaced: {game_path}")

    print()
    print("Patching successful.")
    print()
    print("\n".join(all_results))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
