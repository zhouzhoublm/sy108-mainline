#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Export the locked Linux commit as OpenWrt's checksum-verified source archive."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from workspace import LINUX, DOWNLOADS, OPENWRT

PROJECT = Path(__file__).resolve().parents[2]


def archive_digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def create_archive(dest, version, commit):
    partial = dest.with_suffix(dest.suffix + ".part")
    try:
        with partial.open("wb") as output:
            process = subprocess.Popen(
                ["git", "-C", str(LINUX), "archive", "--format=tar", f"--prefix=linux-{version}/", commit],
                stdout=subprocess.PIPE,
            )
            compressed = subprocess.run(["gzip", "-n"], stdin=process.stdout, stdout=output)
            process.stdout.close()
            if process.wait() or compressed.returncode:
                raise RuntimeError("Linux export failed")
        partial.replace(dest)
    finally:
        partial.unlink(missing_ok=True)


def export_kernel():
    lock = json.loads((PROJECT / "manifests/kernel.json").read_text())
    version = lock["version"]
    series = ".".join(version.split(".")[:2])
    target_ref = lock.get("commit", "HEAD")
    commit = subprocess.check_output(
        ["git", "-C", str(LINUX), "rev-parse", f"{target_ref}^{{commit}}"],
        text=True,
    ).strip()
    if lock.get("commit") and commit != lock["commit"]:
        raise SystemExit(f"Missing locked Linux commit {lock['commit']}")
    archive = lock.get("archive") or f"linux-{version}-sy108-{commit[:12]}.tar.gz"
    dest = DOWNLOADS / archive
    dest.parent.mkdir(parents=True, exist_ok=True)
    expected = lock.get("sha256") if lock.get("archive") == archive else None

    # A restored CI cache can contain a truncated or stale archive. Recreate it
    # from the locked Git commit instead of failing immediately on the cached file.
    if dest.exists() and expected:
        actual = archive_digest(dest)
        if actual != expected:
            print(
                f"Cached Linux archive checksum mismatch; recreating {archive} "
                f"(expected {expected}, got {actual})",
                file=sys.stderr,
            )
            dest.unlink()

    if not dest.exists():
        create_archive(dest, version, commit)

    digest = archive_digest(dest)
    if expected and digest != expected:
        raise SystemExit(
            f"Linux archive checksum mismatch after regeneration: "
            f"expected {expected}, got {digest}"
        )
    kernel_mk = OPENWRT / f"target/linux/sprd/kernel-{series}"
    if kernel_mk.parent.is_dir():
        kernel_mk.write_text(f"LINUX_VERSION-{series} = {version[len(series):]}\nLINUX_KERNEL_HASH-{version} = {digest}\n")
    return {"commit": commit, "archive": archive, "sha256": digest, "path": str(dest)}


if __name__ == "__main__":
    info = export_kernel()
    print(info["path"])
