"""Fixed post-exec RAM workspace checks; emits reviewed JSON fields only."""

import errno
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path

WORKSPACE_BYTES = 4 * 1024**3
RUNNER_ID = 1000
MAX_PROC_BYTES = 65536
CAPABILITY_FIELDS = {
    "CapInh": "inheritable", "CapPrm": "permitted", "CapEff": "effective",
    "CapBnd": "bounding", "CapAmb": "ambient",
}


class ValidationFailure(Exception):
    pass


def read_bounded(path):
    with Path(path).open("rb") as source:
        value = source.read(MAX_PROC_BYTES + 1)
    if len(value) > MAX_PROC_BYTES:
        raise ValidationFailure()
    return value.decode("ascii")


def security_status(text):
    wanted = {"Uid", "Gid", "Groups", "NoNewPrivs", *CAPABILITY_FIELDS}
    fields = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and key in wanted:
            if key in fields:
                raise ValidationFailure()
            fields[key] = value.split()
    if fields.keys() != wanted:
        raise ValidationFailure()
    identities = {name: [int(value) for value in fields[key]] for key, name in [("Uid", "uids"), ("Gid", "gids")]}
    if any(values != [RUNNER_ID] * 4 for values in identities.values()) or fields["Groups"] or fields["NoNewPrivs"] != ["1"]:
        raise ValidationFailure()
    capabilities = {}
    for key, name in CAPABILITY_FIELDS.items():
        if len(fields[key]) != 1 or len(fields[key][0]) > 16:
            raise ValidationFailure()
        value = int(fields[key][0], 16)
        if value != 0:
            raise ValidationFailure()
        capabilities[name] = value
    return {**identities, "groups": [], "capabilities": capabilities, "no_new_privs": True}


def mount_status(path, text):
    selected = None
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 10 or "-" not in fields:
            continue
        separator = fields.index("-")
        if separator + 3 >= len(fields):
            continue
        mountpoint = Path(fields[4].replace("\\040", " "))
        if path.is_relative_to(mountpoint) and (selected is None or len(str(mountpoint)) > len(str(selected[0]))):
            selected = (mountpoint, fields[separator + 1], set(fields[5].split(",")))
    if selected is None:
        raise ValidationFailure()
    mountpoint, filesystem, flags = selected
    if mountpoint != Path("/work") or filesystem != "tmpfs" or not {"nosuid", "nodev"}.issubset(flags) or "noexec" in flags:
        raise ValidationFailure()
    return {"filesystem": filesystem, "nosuid": True, "nodev": True, "exec": True}


def expect_eperm(action):
    try:
        action()
    except OSError as error:
        if error.errno == errno.EPERM:
            return True
    raise ValidationFailure()


def filesystem_operations(path):
    with tempfile.TemporaryDirectory(prefix="yucca-ram-validation-", dir=path) as directory:
        root = Path(directory)
        source, link, executable = root / "source", root / "hardlink", root / "executable"
        payload = b"yucca-ram-validation\n"
        source.write_bytes(payload)
        source.chmod(0o600)
        os.link(source, link)
        executable.write_bytes(b"#!/bin/sh\nexit 0\n")
        executable.chmod(0o700)
        for entry in [source, link, executable]:
            metadata = entry.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != RUNNER_ID or metadata.st_gid != RUNNER_ID:
                raise ValidationFailure()
        if source.read_bytes() != payload or link.read_bytes() != payload or source.stat().st_ino != link.stat().st_ino or source.stat().st_nlink != 2:
            raise ValidationFailure()
        result = subprocess.run([str(executable)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2, check=False)
        if result.returncode != 0:
            raise ValidationFailure()
    return {"write_read": True, "hardlink": True, "execute": True, "new_files_owned_by_runner": True}


def validate():
    process = security_status(read_bounded("/proc/self/status"))
    # An unexpectedly successful transition stops this process immediately.
    process["setuid_zero_denied_eperm"] = expect_eperm(lambda: os.setuid(0))
    mounts = read_bounded("/proc/self/mountinfo")
    work = Path("/work").resolve(strict=True)
    device = work.stat().st_dev
    result = {}
    for label, raw in [("work", "/work"), ("github_workspace", os.environ["GITHUB_WORKSPACE"]), ("runner_temp", os.environ["RUNNER_TEMP"])]:
        path = Path(raw).resolve(strict=True)
        metadata, filesystem = path.stat(), os.statvfs(path)
        size = filesystem.f_frsize * filesystem.f_blocks
        if not path.is_relative_to(work) or not path.is_dir() or metadata.st_dev != device or metadata.st_uid != RUNNER_ID or metadata.st_gid != RUNNER_ID or size != WORKSPACE_BYTES:
            raise ValidationFailure()
        result[label] = {**mount_status(path, mounts), "size_bytes": size, "uid": metadata.st_uid, "gid": metadata.st_gid, **filesystem_operations(path)}
    return {"schema": 1, "kind": "ram_workspace_validation", "success": True, "process": process, "paths": result}


if __name__ == "__main__":
    try:
        record = validate()
    except (ValidationFailure, OSError, ValueError, KeyError, UnicodeError, subprocess.SubprocessError):
        print('YUCCA_RAM_VALIDATION_JSON {"schema":1,"kind":"ram_workspace_validation","success":false,"error":"RAM workspace validation failed"}', flush=True)
        raise SystemExit(1) from None
    print("YUCCA_RAM_VALIDATION_JSON " + json.dumps(record, sort_keys=True), flush=True)
