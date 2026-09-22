import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Patch configuration
# ---------------------------------------------------------------------------

def load_patches() -> list[dict[str, Any]]:
    """Load and decode patches.json."""
    candidates = (
        Path.cwd() / "patches.json",
        Path(__file__).resolve().parent / "patches.json",
    )

    for path in candidates:
        if path.is_file():
            break
    else:
        raise FileNotFoundError("patches.json not found")

    def decode(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                key: (
                    bytes.fromhex(value)
                    if key in ("original", "modified")
                    else decode(value)
                )
                for key, value in node.items()
            }

        if isinstance(node, list):
            return [decode(value) for value in node]

        return node

    return decode(
        json.loads(path.read_text(encoding="utf-8"))
    )["patches"]


PATCHES = load_patches()


# ---------------------------------------------------------------------------
# Byte utilities
# ---------------------------------------------------------------------------

def find_matches(data: bytes, pattern: bytes) -> list[int]:
    """Return every offset where pattern occurs in data."""
    matches = []
    start = 0

    while True:
        offset = data.find(pattern, start)

        if offset == -1:
            return matches

        matches.append(offset)
        start = offset + 1


def hex_bytes(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


def describe_mismatch(
    data: bytes,
    original: bytes,
    modified: bytes,
) -> str:
    """Explain why original bytes were not found."""
    # Check whether the patch has already been applied.
    if modified != original:
        already = find_matches(data, modified)

        if already:
            locations = ", ".join(
                f"0x{offset:X}"
                for offset in already[:3]
            )

            return (
                f"the patched bytes are already present "
                f"@ {locations}"
            )

    size = len(original)
    candidates = []

    # Find the closest matching prefix/suffix.
    for length in range(size - 1, 2, -1):
        candidates.extend(
            find_matches(data, original[:length])[:5]
        )

        candidates.extend(
            hit - (size - length)
            for hit in find_matches(
                data,
                original[size - length:],
            )[:5]
        )

        if candidates:
            break

    best_start = None
    best_score = 0

    for start in candidates:
        window = data[max(start, 0):start + size]

        if start < 0 or len(window) != size:
            continue

        score = sum(
            actual == expected
            for actual, expected
            in zip(window, original)
        )

        if score > best_score:
            best_start = start
            best_score = score

    if best_start is None:
        return "no similar bytes found anywhere in the file"

    found = data[best_start:best_start + size]

    marker = " ".join(
        "  " if actual == expected else "^^"
        for actual, expected
        in zip(found, original)
    )

    return (
        f"closest match @ 0x{best_start:X} "
        f"({best_score}/{size} bytes agree)\n"
        f"      expected: {hex_bytes(original)}\n"
        f"      found:    {hex_bytes(found)}\n"
        f"                  {marker}"
    )


# ---------------------------------------------------------------------------
# Patch application
# ---------------------------------------------------------------------------

def patch_candidates(
    step: dict[str, Any],
) -> list[tuple[bytes, bytes]]:
    """Return the primary patch followed by its fallbacks."""
    modified = step["modified"]

    candidates = [
        (step["original"], modified)
    ]

    for fallback in step.get("fallbacks", []):
        candidates.append(
            (
                fallback["original"],
                fallback.get("modified", modified),
            )
        )

    return candidates


def apply_step(
    data: bytearray,
    label: str,
    step: dict[str, Any],
) -> int:
    """Apply one patch step and return its offset."""
    candidates = patch_candidates(step)

    # Validate lengths before modifying anything.
    for original, modified in candidates:
        if len(original) != len(modified):
            raise ValueError(
                f"{label}: original and modified "
                f"byte lengths differ."
            )

    selected = None
    offset = None

    for original, modified in candidates:
        matches = find_matches(data, original)

        if len(matches) > 1:
            locations = ", ".join(
                f"0x{value:X}"
                for value in matches[:8]
            )

            raise ValueError(
                f"{label}: expected exactly 1 match, "
                f"found {len(matches)} @ {locations}."
            )

        if matches:
            selected = (original, modified)
            offset = matches[0]
            break

    if selected is None:
        original, modified = candidates[0]

        fallback_count = len(candidates) - 1

        suffix = (
            f" (also tried {fallback_count} "
            f"fallback{'s' if fallback_count != 1 else ''})"
            if fallback_count
            else ""
        )

        raise ValueError(
            f"{label}: expected bytes not found{suffix}.\n"
            f"    {describe_mismatch(data, original, modified)}"
        )

    _, modified = selected

    data[offset:offset + len(modified)] = modified

    # Verify the modification.
    if data[offset:offset + len(modified)] != modified:
        raise ValueError(
            f"{label}: verification failed."
        )

    return offset


def apply_patch(
    data: bytearray,
    patch: dict[str, Any],
) -> list[int]:
    """Apply every step in a patch and return their offsets."""
    steps = patch.get("steps") or [patch]
    offsets = []

    for number, step in enumerate(steps, start=1):
        label = f'"{patch["name"]}"'

        if len(steps) > 1:
            note = (
                f': {step["note"]}'
                if "note" in step
                else ""
            )

            label += (
                f" (step {number}/{len(steps)}{note})"
            )

        offsets.append(
            apply_step(data, label, step)
        )

    return offsets


def patch_file(
    source_path: Path,
    patches: list[dict[str, Any]],
) -> tuple[bytearray, list[str]]:
    """
    Patch a file in memory.

    Nothing is written to disk.
    """
    data = bytearray(source_path.read_bytes())
    results = []

    for patch in patches:
        offsets = apply_patch(data, patch)

        results.append(
            f"{source_path.name}: "
            f"steps({len(offsets)}): "
            f"{patch['name']}"
        )

    return data, results


# ---------------------------------------------------------------------------
# File management
# ---------------------------------------------------------------------------

def find_file(
    folder: Path,
    name: str,
) -> Path | None:
    """Find a file by name, case-insensitively."""
    target = name.casefold()

    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.name.casefold() == target:
            return path

    return None


def ensure_original_backup(
    game_path: Path,
) -> tuple[Path, bool]:
    """
    Ensure a pristine original backup exists.

    Returns:
        (backup_path, created)
    """
    backup_dir = game_path.parent / "original"
    backup_dir.mkdir(exist_ok=True)

    backup_path = backup_dir / game_path.name

    if backup_path.exists():
        return backup_path, False

    shutil.copy2(game_path, backup_path)

    return backup_path, True


def create_mods_folder(
    game_path: Path,
) -> Path:
    """Create and return the mods folder."""
    mods_dir = game_path.parent / "mods"
    mods_dir.mkdir(exist_ok=True)

    return mods_dir


# ---------------------------------------------------------------------------
# Patch organization
# ---------------------------------------------------------------------------

def group_patches(
    patches: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group patches by target filename."""
    grouped = {}

    for patch in patches:
        grouped.setdefault(
            patch["file"],
            [],
        ).append(patch)

    return grouped


def build_jobs(
    folder: Path,
    grouped: dict[str, list[dict[str, Any]]],
) -> list[tuple[Path, list[dict[str, Any]]]]:
    """Find every target file and build patch jobs."""
    jobs = []

    for name, patches in grouped.items():
        game_path = find_file(folder, name)

        if game_path is None:
            raise FileNotFoundError(
                f"{name} not found in {folder}"
            )

        jobs.append((game_path, patches))

    return jobs


# ---------------------------------------------------------------------------
# Main patching pipeline
# ---------------------------------------------------------------------------

def prepare_patch(
    game_path: Path,
    patches: list[dict[str, Any]],
) -> tuple[Path, bytearray, list[str], bool]:
    """
    Create the original backup if necessary and patch it in memory.

    Returns:
        backup path,
        patched bytes,
        result messages,
        whether the backup was newly created.
    """
    create_mods_folder(game_path)

    backup_path, created = ensure_original_backup(game_path)

    data, results = patch_file(
        backup_path,
        patches,
    )

    return (
        backup_path,
        data,
        results,
        created,
    )


def write_patched_files(
    patched: list[tuple[Path, bytearray]],
) -> None:
    """Write all successfully prepared files to disk."""
    for game_path, data in patched:
        game_path.write_bytes(data)
        print(f"Replaced: {game_path}")


def print_patch_list(
    grouped: dict[str, list[dict[str, Any]]],
) -> None:
    print("Files to modify:")

    for name, patches in grouped.items():
        noun = (
            "patch"
            if len(patches) == 1
            else "patches"
        )

        print(
            f"  {name} "
            f"({len(patches)} {noun})"
        )

    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Cyberstrom v1.3 offline EXE patcher."
        )
    )

    parser.add_argument(
        "folder",
        type=Path,
        help="Game folder containing the files to patch",
    )

    args = parser.parse_args()
    folder = args.folder

    if not folder.is_dir():
        print(
            f"Error: folder does not exist: {folder}",
            file=sys.stderr,
        )
        return 1

    try:
        patches = load_patches()
    except Exception as exc:
        print(
            f"Error loading patches.json: {exc}",
            file=sys.stderr,
        )
        return 1

    grouped = group_patches(patches)

    print_patch_list(grouped)

    # Locate everything before modifying anything.
    try:
        jobs = build_jobs(
            folder,
            grouped,
        )
    except FileNotFoundError as exc:
        print(
            f"Error: {exc}",
            file=sys.stderr,
        )
        return 1

    for game_path, _ in jobs:
        print(f"Found: {game_path}")

    print()

    # Patch every file in memory.
    # Nothing is replaced until ALL jobs succeed.
    patched = []
    all_results = []

    for game_path, file_patches in jobs:
        try:
            (
                backup_path,
                data,
                results,
                created,
            ) = prepare_patch(
                game_path,
                file_patches,
            )

        except Exception as exc:
            print(
                "Patch aborted. "
                "No files were modified.",
                file=sys.stderr,
            )

            print(
                f"{game_path.name}: {exc}",
                file=sys.stderr,
            )

            return 1

        if created:
            print(
                f"Created original backup: "
                f"{backup_path}"
            )
        else:
            print(
                f"Using original backup: "
                f"{backup_path}"
            )

        patched.append(
            (game_path, data)
        )

        all_results.extend(results)

    # All patches succeeded in memory.
    write_patched_files(patched)

    print()
    print("Patching successful.")
    print()
    print(
        f"Applied {len(all_results)} "
        f"patch{'es' if len(all_results) != 1 else ''}."
    )
    print()
    print(
        "The tool can be safely run again."
    )
    print()

    for result in all_results:
        print(f"  {result}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
