#!/usr/bin/env python3
"""Restore trusted, version-locked GitHub workflows for supported TimePilot releases.

Standalone, no third-party packages, no dependency downloads, no runtime data writes.
Never updates the release manifest or "resigns" unknown modifications.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
import tomllib
import uuid
import zlib

SUPPORTED_VERSIONS = ("0.38.8", "0.38.9", "0.38.10", "0.39.0", "0.39.1", "0.39.2", "0.39.3")
# Expected release bytes independently copied from the verified v0.38.8 ZIP.
# The repair first verifies both the embedded bytes AND the local release manifest.
EMBEDDED = {
    '.github/workflows/ci.yml': {
        "sha256": '23d7bb80a5f30ad3b606325e84b49938ba3fb0bad7d1607701e140ee42610036',
        "bytes": 14541,
        "zlib_b64": (
            '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'
            'RVOlz6jk79tzLjrFtEg7ePI/stB8DA=='
        ),
    },
    '.github/workflows/offline-release.yml': {
        "sha256": 'e2558dbffe8e9f15048368060ab6ae6aec4df3cd09318547f42b14b1c2884ebc',
        "bytes": 1954,
        "zlib_b64": (
            'eNqdVW1rGzkQ/u5fMXCFpBBtEjsHwUehac4FQ67JXdM7uFKMvDvbVaKVdNLIrtvrf+9o32ynrxT2w66keWaeZ54dGVnjFG5VjTdKW4Lc'
            '1k4jIZD0b5GE05JK62uwZamVQfCoUQaEw1qaKPXj0cia6Qhgbf19qe16UajgJOVVWgRQxkUK7TuAj8ag778ACgy5V44UY8DBPxWirmwM'
            'fXYYsh/andT/zm/AoYfrl8eXN68eHwxwtHFMJq+synFY9PhfVB6LKZCP22XbZA3bWgAE1DK3QZye7S3GZTQUxfgsO9nfWCtT2HUQ45Px'
            'eDS6s8sGrVdQdIq1GZh5EInlow8fOlGyVg34+LE5QdwDG0nUykTCMIXT8UmzEQjdUCeXE9KmzJvyj/MK83sOe7o6+8qJgBSdcBuqrHm6'
            '+nVgsFZU7bJvT4gV+tC2Y5Kdjvk5+CausQVuc3+OmvZ3MMdnWzjTWO9v9KrcAL5jXBaG0DvP6nmQpoBgo99pZahQ6yksZai27Y2M+/9n'
            'PEDkcKBqZz1B2ITfZAjYvmZdNQtlSvt6Onnz5Mnh5Oh0zM+OlQac1qDheNXUyUGBpGZbMkLmNiBEWyP3Vm8eUJu3Z8F5e4fMrkCHpkCT'
            'KwxMFZZR6aL7J77PsedVg1MOujpA+N7gNRr2FL2jLuwXuK0U5wmNqiYVzAEbWGqb32MBbGrFtcn8Xr5FkRazu8AJOMDjSuEaiwEp9YJ9'
            'XSsijiQLVGHXnP6vzOCFhTqSXGoE42peD1bHlDZxvZxnX278FjYFpTJ+WIq+NRy4SIGLCqWmitvyINWzJPQRewx9rgIeQdvMhpYkW6u8'
            'UcbFpVahGkYdz5nv15JWhkqevZpf/b64fv78av5itvhrdjW7eDnLhpCtMXorUKeiICukzyu1Qm6JV8TCpD/7p6z/wLIdnUWHv1idTM7P'
            'W+/2KYf5z5vZ5Dw7X1xe/3FzNbud9WSy92w6ITqvieGaGPzvLd8eGX/zMOLepx+PK0ipjkvFzuvTNyZ7oMdFVwZfHeTVsjVNas6fFwyV'
            'Y2Iz0NyfQtFpKwsmQqrktW+OoiZXGrMuURVNcQMT8dXB3GkrGW1PbfhB2fZC9pXZ2VIlz1JRKo1BlDYavq/Qe+tHnwBBTVkC'
        ),
    },
}


# Version-locked trust anchor for the current release workflow.
UPDATED_OFFLINE_RELEASE = {"sha256": "62a4353ec1593dd689ea148c581b7e9b34e20f6fd3d71811f2a72eb00a934fb8", "bytes": 2216, "zlib_b64": 'eNqVVW1PIzcQ/p5fMdpSJanwBgKVUFCky9FcG4kCBY5KRWi12Z1lDbu2a3sTUo7/3vG+JYG7o5X2w3rsmXlmnmdsEeY4gmue4wXPpIVI5ipDi2BDfY+WqSy0idQ5yCTJuEDQmGFoEHp5KIow63c6Uow6AEupH5NMLoOYGxXaKHVGAC5UYU31D6ALIVA3K4AYTaS5spxiQPfPFDFLZWGa7NBm78mN1H/NLkChhvOrwcnF5363DWdXioqJUskjbI0a/y64xngEVhdrsyyzmjUWAAZ5GEnD9g+3jMW8ELZgw0N/b3tjyUUsl4YN94bDTudBzstoTQdZ3bEqA1VumKty5/m5bopfdQNeXsoTljiQhWU5F4VFM4L94V65YSyqFifBMW4zjEr4gyjF6JHcPiwOv3HCoC0UUyubSvFh8XNbwZLbdLP66gRboDYVHQf+/pC+7nfjChnjOvfbqG5/I+bwcB1OlNK7Qc2TFeATxaXGWNRKU/c0hCIGIwu9QaVJMctGMA9Nuqa3oLhf3tQBLIIuz5XUFszKHIfGYPXr12gCLhJ5Ozq4G497B7v7Q/o2pNTGqQRqBosSJzkZG2YkS4rgqxUwVmEkbrPVq9Jm1VlQWj4gVRejQhGjiDgaKhXmBc/ieiber7GpKwfFFdQ4gOlG4DkK0pR9srXbD3Cdcspjyq4KB5gcVjDPZPSIMZCoOWELo8fwHpkz+g+GEpCDxgXHJcZtJMcF6Trn1pKnlWBTrMlpptKHMwl5YcN5hiBUTnYjs8KldbWezPyvE78O65wcjP/cioYacgycY5BimNmUaHmV6qNr9C5pDHXEDe5CRWZZVmhlzqOyM6qYZ9yk7VVH98z7WJylRfLx8+z0l+D806fT2dk0uJyeTidXU791WQujkYKtu8isZKGOUr5AokRzS41xk/3/pH8zvbyanZ+NvZ3e2ymgOrOMz48TLUlCoU1pAfXeBS2PlSah9OpjfibD2PTcRs9Tq1rCvtv1+r7GMA4sPtkeqVnGXNyPvcIm7Mjr92+9+rB3d+vVs+bd9bt97y3S4Hry69hbEFyXO4Huj6YL3k6968EXurOBxdD1t90nlye/zW6mY699uILFznPt9hKcnP9+cTq9njZM+P9w5b072jXtQc1DsNg7ODqqZryhxtupM3tkrOeOtU9mexdoSS+pT2u6mMHT6HpsBi1AV/XLIOE0j02ycvS8VzKZ1FnpRbWaz6tZcpr9Y0LTFaED31a1fTkXyvFHuC1PyPbdG7rM5V4f5RrZlsO++VLVTSRpbMsPYIOOn77KwtbppjEbRp7Qi8ISnqFhiSwEvdqotdSdfwF316kn'}

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plan_repair(root: Path) -> list[dict]:
    manifest_path = root / "docs/RELEASE_MANIFEST.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Missing or unsafe local docs/RELEASE_MANIFEST.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    if (manifest.get("version") != project.get("project", {}).get("version") or
            manifest.get("version") not in SUPPORTED_VERSIONS):
        raise ValueError("Refusing different/mixed versions; supported source releases: " + ", ".join(SUPPORTED_VERSIONS))
    if not isinstance(manifest.get("files"), dict):
        raise ValueError("Malformed release file manifest")
    tasks = []
    for name, original_spec in EMBEDDED.items():
        spec = (UPDATED_OFFLINE_RELEASE if name.endswith("/offline-release.yml")
                and manifest["version"] in ("0.38.10", "0.39.0", "0.39.1", "0.39.2", "0.39.3") else original_spec)
        source = zlib.decompress(base64.b64decode("".join(spec["zlib_b64"])))
        if len(source) != spec["bytes"] or sha256(source) != spec["sha256"]:
            raise ValueError("Embedded release data failed SHA256 check: " + name)
        if manifest["files"].get(name) != {"sha256": spec["sha256"], "bytes": spec["bytes"]}:
            raise ValueError("Local release manifest differs from trusted published workflow: " + name)
        dest = root.joinpath(*name.split("/"))
        for parent in (root / ".github", root / ".github/workflows"):
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                raise ValueError("Unsafe parent directory: " + str(parent))
        if dest.is_symlink() or (dest.exists() and not dest.is_file()):
            raise ValueError("Unsafe target: " + name)
        old = dest.read_bytes() if dest.exists() else None
        state = "missing" if old is None else ("unchanged" if old == source else "changed")
        tasks.append({"name": name, "dest": dest, "old": old, "new": source, "state": state})
    return tasks


def repair(root: Path, *, dry_run: bool = False, backup_root: Path | None = None) -> dict:
    root = Path(root).expanduser().resolve()
    tasks = plan_repair(root)  # Verify ALL entries before modifying ANY.
    result = {
        "release": tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"], "project": str(root),
        "dry_run": dry_run,
        "would_restore": [t["name"] for t in tasks if t["state"] == "missing"],
        "would_repair": [t["name"] for t in tasks if t["state"] == "changed"],
        "unchanged": [t["name"] for t in tasks if t["state"] == "unchanged"],
        "restored": [], "repaired": [], "backup_dir": None,
        "python_environment_changed": False, "runtime_data_changed": False,
    }
    if dry_run:
        return result
    changed = [t for t in tasks if t["state"] == "changed"]
    if changed:
        base = (Path(backup_root).expanduser().resolve() if backup_root
                else Path.home() / "TimePilot-source-repair-backups")
        # Keep backups away from the tracked source (and outside user runtime).
        if base == root or root in base.parents or base in root.parents:
            raise ValueError("Backup path must be separate from the source tree")
        base.mkdir(parents=True, exist_ok=True)
        target = base / ("source_" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                         + "_" + uuid.uuid4().hex[:8])
        target.mkdir(mode=0o700)
        hashes = {}
        for item in changed:
            path = target.joinpath(*item["name"].split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(item["old"])
            if sha256(path.read_bytes()) != sha256(item["old"]):
                raise OSError("Backup verification failed: " + item["name"])
            hashes[item["name"]] = sha256(item["old"])
        (target / "original_sha256.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
        result["backup_dir"] = str(target)
    for item in tasks:
        if item["state"] == "unchanged":
            continue
        dest = item["dest"]
        for parent in (root / ".github", root / ".github/workflows"):
            if parent.is_symlink():
                raise ValueError("Parent became symlink during repair: " + str(parent))
            parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink():
            raise ValueError("Target became a symlink: " + str(dest))
        fd, temporary = tempfile.mkstemp(prefix=".tp-workflow-", dir=dest.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(item["new"])
                f.flush()
                os.fsync(f.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, dest)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)
        if sha256(dest.read_bytes()) != sha256(item["new"]):
            raise OSError("Restored file failed verification: " + item["name"])
        result["repaired" if item["state"] == "changed" else "restored"].append(item["name"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="TimePilot version-locked Mac dotfile recovery")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Existing TimePilot project directory")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backup-dir", type=Path, default=None)
    args = parser.parse_args()
    try:
        result = repair(args.root, dry_run=args.dry_run, backup_root=args.backup_dir)
        print(json.dumps({"status": "DRY_RUN" if args.dry_run else "PASS", **result}, ensure_ascii=False, indent=2))
        if not args.dry_run:
            print("NEXT: bash TIMEPILOT.sh source-check")
        return 0
    except (OSError, KeyError, ValueError, TypeError) as exc:
        print("[BLOCKED] " + str(exc), file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
