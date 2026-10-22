"""Fixed Survey workload on native runner shapes; no caches or RAM workspace."""

import hashlib
import json
import os
import platform
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SOURCE_COMMIT = "d9a9649227cb44844506be8f25ee5d6bf6846b5f"
LOCK_SHA256 = "4fd09bb65a6ffcd37919e89fb6369fa90c77d677efa9a0f54963874947cb290a"
NODE_VERSION = "v24.12.0"
PNPM_VERSION = "12.4.2"
NODE_BASE = "https://nodejs.org/dist/v24.12.0/"
NODE_ARCHIVE = "node-v24.12.0-linux-x64.tar.xz"
NODE_SHA256 = "bdebee276e58d0ef5448f3d5ac12c67daa963dd5e0a9bb621a53d1cefbc852fd"
HEADERS_ARCHIVE = "node-v24.12.0-headers.tar.xz"
HEADERS_SHA256 = "a432e8935507a98ec0fc4c93b48199e83b4bec5b8db7e0caedf058be0c5631bf"
SHAPES = {"lite", "basic", "standard-1", "standard-2", "standard-3", "standard-4", "github"}
STAGE_TIMEOUT_SECONDS = 480
SAMPLE_SECONDS = 1.0
TERMINATE_SECONDS = 2
EXPECTED_TESTS = 488
OUTPUT_LIMIT_BYTES = 256 * 1024
CPU_KEYS = {"usage_usec", "user_usec", "system_usec", "nr_periods", "nr_throttled", "throttled_usec", "nr_bursts", "burst_usec"}
MEMORY_KEYS = {"low", "high", "max", "oom", "oom_kill", "oom_group_kill"}
STAGES = {"setup_node_download", "setup_node_extract", "setup_pnpm", "install", "build_frontend", "unit_tests"}
PREFIX = "YUCCA_FRONTEND_SHAPES_JSON "


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def counters(path, allowed):
    result = {}
    for line in read(path).splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] in allowed and fields[1].isdecimal():
            result[fields[0]] = int(fields[1])
    return result


def counter_delta(before, after):
    return {key: after[key] - before[key] for key in before.keys() & after.keys()}


def cgroup_scope():
    root = Path("/sys/fs/cgroup")
    relative = next((line.partition("::")[2] for line in read("/proc/self/cgroup").splitlines() if line.startswith("0::")), "")
    current = (root / relative.lstrip("/")).resolve()
    if not current.is_relative_to(root):
        return root
    while current != root and not (current / "memory.events").is_file():
        current = current.parent
    return current


def integer_file(path):
    value = read(path)
    return int(value) if value.isdecimal() else None


def filesystem(path):
    path = Path(path).resolve()
    result, length = {}, -1
    for line in read("/proc/self/mountinfo").splitlines():
        fields = line.split()
        if len(fields) < 7 or "-" not in fields:
            continue
        mount = fields[4].replace("\\040", " ")
        if path.is_relative_to(mount) and len(mount) > length:
            separator = fields.index("-")
            if len(fields) <= separator + 3:
                continue
            options = fields[5].split(",") + fields[separator + 3].split(",")
            result = {"fstype": fields[separator + 1], "options": sorted({option for option in options if option in {"nosuid", "nodev", "noexec", "rw", "ro"} or re.fullmatch(r"compress(?:-force)?=(?:zstd|zlib|lzo)(?::[0-9]{1,2})?", option)})}
            length = len(mount)
    disk = os.statvfs(path)
    return {**result, "total_bytes": disk.f_blocks * disk.f_frsize, "available_bytes": disk.f_bavail * disk.f_frsize, "files_available": disk.f_favail}


def group_memory(stat, group, page_size):
    if ") " not in stat:
        return 0, 0
    fields = stat.rsplit(") ", 1)[1].split()
    try:
        if int(fields[2]) == group:
            return max(0, int(fields[21])) * page_size, 1
    except (ValueError, IndexError):
        pass
    return 0, 0


def sample_group(group):
    total, count = 0, 0
    page_size = os.sysconf("SC_PAGE_SIZE")
    for entry in Path("/proc").iterdir():
        if entry.name.isdecimal():
            resident, processes = group_memory(read(entry / "stat"), group, page_size)
            total += resident
            count += processes
    return total, count


def test_counts(text):
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    matches = re.findall(r"\bTests\s+(\d+) passed\s+\((\d+)\)", text)
    if not matches:
        return {"passed": None, "total": None, "expected": EXPECTED_TESTS}
    passed, total = matches[-1]
    return {"passed": int(passed), "total": int(total), "expected": EXPECTED_TESTS}


class WorkloadFailure(Exception):
    def __init__(self, code=1):
        self.code = code if 1 <= code <= 255 else 1


class FrontendBenchmark:
    def __init__(self):
        shape = os.environ["BENCHMARK_SHAPE"]
        trial = os.environ["BENCHMARK_TRIAL"]
        revision = os.environ["GITHUB_SHA"]
        if shape not in SHAPES or trial not in {"1", "2", "3"} or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            raise WorkloadFailure()
        self.workspace = Path.cwd()
        self.temp = Path(os.environ["RUNNER_TEMP"])
        self.directory = Path(tempfile.mkdtemp(prefix="yucca-frontend-", dir=self.temp))
        self.output = self.temp / "yucca-frontend-shapes.jsonl"
        self.common = {"schema": 1, "workload": "survey-frontend", "backend": "github" if shape == "github" else "cloudflare", "shape": shape, "trial": int(trial), "workflow_commit": revision, "source_commit": SOURCE_COMMIT, "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"]}
        self.scope = cgroup_scope()
        self.environment = {**os.environ, "XDG_CACHE_HOME": str(self.directory / "xdg-cache"), "XDG_CONFIG_HOME": str(self.directory / "xdg-config"), "XDG_DATA_HOME": str(self.directory / "xdg-data"), "XDG_STATE_HOME": str(self.directory / "xdg-state"), "PNPM_HOME": str(self.directory / "pnpm-home"), "PNPM_CONFIG_CACHE_DIR": str(self.directory / "pnpm-cache"), "PNPM_CONFIG_STATE_DIR": str(self.directory / "pnpm-state"), "PNPM_CONFIG_STORE_DIR": str(self.directory / "store"), "PNPM_CONFIG_SIDE_EFFECTS_CACHE": "false", "npm_config_cache": str(self.directory / "npm-cache")}
        self.stage_seconds = {}

    def emit(self, kind, **fields):
        body = json.dumps({**self.common, "kind": kind, **fields}, sort_keys=True)
        with self.output.open("a") as target:
            target.write(body + "\n")
            target.flush()
        print(PREFIX + body, flush=True)

    def snapshot(self):
        return {"children": resource.getrusage(resource.RUSAGE_CHILDREN), "observer": resource.getrusage(resource.RUSAGE_SELF), "cpu": counters(self.scope / "cpu.stat", CPU_KEYS), "memory_events": counters(self.scope / "memory.events", MEMORY_KEYS)}

    def stage(self, name, arguments, timeout=STAGE_TIMEOUT_SECONDS):
        if name not in STAGES or not 0 < timeout <= STAGE_TIMEOUT_SECONDS:
            raise WorkloadFailure()
        self.emit("stage_started", stage=name, timeout_seconds=timeout)
        before, started = self.snapshot(), time.perf_counter()
        log = self.directory / (name + ".log")
        code, timed_out, peak, processes = 1, False, 0, 0
        with log.open("wb") as target:
            process = subprocess.Popen(arguments, stdout=target, stderr=subprocess.STDOUT, env=self.environment, start_new_session=True)
            deadline = time.monotonic() + timeout
            while True:
                rss, count = sample_group(process.pid)
                if rss > peak:
                    peak, processes = rss, count
                try:
                    code = process.wait(timeout=max(0.001, min(SAMPLE_SECONDS, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() < deadline:
                        continue
                    timed_out = True
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=TERMINATE_SECONDS)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
                    code = 124
                    break
        elapsed, after = time.perf_counter() - started, self.snapshot()
        self.stage_seconds[name] = elapsed
        with log.open("rb") as target:
            size = log.stat().st_size
            target.seek(max(0, size - OUTPUT_LIMIT_BYTES))
            text = target.read(OUTPUT_LIMIT_BYTES).decode(errors="replace")
        tests = test_counts(text) if name == "unit_tests" else None
        validation_failed = tests is not None and (tests["passed"] != EXPECTED_TESTS or tests["total"] != EXPECTED_TESTS)
        memory_events = counter_delta(before["memory_events"], after["memory_events"])
        oom_kills = memory_events.get("oom_kill")
        record = {"stage": name, "seconds": round(elapsed, 6), "exit_code": code, "timed_out": timed_out, "validation_failed": validation_failed, "oom_kill_observed": oom_kills > 0 if oom_kills is not None else None, "child_cpu": {"user_seconds": round(after["children"].ru_utime - before["children"].ru_utime, 6), "system_seconds": round(after["children"].ru_stime - before["children"].ru_stime, 6)}, "sampled_group_peak_rss_bytes": peak, "sampled_group_peak_processes": processes, "child_maxrss_bytes_before": before["children"].ru_maxrss * 1024, "child_maxrss_bytes_after": after["children"].ru_maxrss * 1024, "cgroup_cpu_delta": counter_delta(before["cpu"], after["cpu"]), "cgroup_memory_events_delta": memory_events, "cgroup_memory_peak_bytes": integer_file(self.scope / "memory.peak"), "cgroup_memory_current_bytes_after": integer_file(self.scope / "memory.current"), "workspace_filesystem_after": filesystem(self.workspace)}
        if tests is not None:
            record["tests"] = tests
        record["observer_cpu"] = {"user_seconds": round(after["observer"].ru_utime - before["observer"].ru_utime, 6), "system_seconds": round(after["observer"].ru_stime - before["observer"].ru_stime, 6)}
        self.emit("stage", **record)
        log.unlink()
        if code or validation_failed:
            raise WorkloadFailure(124 if timed_out else 128 - code if code < 0 else code or 1)

    def version(self, arguments):
        return subprocess.check_output(arguments, env=self.environment, timeout=30, text=True, stderr=subprocess.DEVNULL).strip()

    def check_source(self):
        if self.version(["git", "rev-parse", "HEAD"]) != SOURCE_COMMIT:
            raise WorkloadFailure()
        lock = hashlib.sha256((self.workspace / "pnpm-lock.yaml").read_bytes()).hexdigest()
        package_manager = json.loads((self.workspace / "package.json").read_text()).get("packageManager", "").split("+", 1)[0]
        if lock != LOCK_SHA256 or package_manager != "pnpm@" + PNPM_VERSION or os.getuid() == 0:
            raise WorkloadFailure()
        for path in [self.workspace, self.temp]:
            if filesystem(path).get("fstype") in {"tmpfs", "ramfs"}:
                raise WorkloadFailure()
        if any(key in self.environment for key in ["RAYON_NUM_THREADS", "NODE_OPTIONS"]):
            raise WorkloadFailure()
        self.emit("environment", lock_sha256=lock, cache_policy={"actions_cache": False, "fresh_store": True, "fresh_metadata": True, "side_effects_cache": False, "workspace": "disk", "rayon_override": False, "integrity_checks": True, "lifecycle_scripts": True}, hardware={"uid": os.getuid(), "gid": os.getgid(), "architecture": platform.machine(), "kernel": platform.release(), "cpu_count": os.cpu_count(), "affinity": sorted(os.sched_getaffinity(0)), "cpu_models": sorted({line.split(":", 1)[1].strip() for line in read("/proc/cpuinfo").splitlines() if line.startswith("model name")}), "cgroup_cpu_max": read(self.scope / "cpu.max"), "cgroup_memory_max": read(self.scope / "memory.max"), "mem_total_bytes": next((int(line.split()[1]) * 1024 for line in read("/proc/meminfo").splitlines() if line.startswith("MemTotal:")), None), "workspace_filesystem": filesystem(self.workspace), "temporary_filesystem": filesystem(self.temp)}, measurement_notes={"sample_seconds": SAMPLE_SECONDS, "group_rss": "Sum of live process-group RSS; shared pages double counted and short-lived children may be missed", "cgroup_peak": "Lifetime cgroup high-water mark including runner and setup; never reset", "child_maxrss": "Cumulative largest-child high-water mark, not process-tree sum", "setup": "Existing Cloudflare image Node verified and reused; pinned complete archive when absent or on GitHub, same Node/pnpm/header versions; setup excluded from workload total"})

    def setup(self):
        reuse = self.common["backend"] == "cloudflare" and shutil.which("node", path=self.environment.get("PATH", "")) is not None
        if reuse and self.version(["node", "--version"]) != NODE_VERSION:
            raise WorkloadFailure()
        archive, checksum = (HEADERS_ARCHIVE, HEADERS_SHA256) if reuse else (NODE_ARCHIVE, NODE_SHA256)
        payload, node = self.directory / "node.tar.xz", self.directory / "node"
        node.mkdir()
        self.stage("setup_node_download", ["curl", "--fail", "--silent", "--show-error", "--location", "--proto", "=https", "--proto-redir", "=https", "--retry", "2", "--connect-timeout", "10", "--max-time", "120", "--max-filesize", str(64 * 1024**2), NODE_BASE + archive, "--output", str(payload)], 420)
        if hashlib.sha256(payload.read_bytes()).hexdigest() != checksum:
            raise WorkloadFailure()
        self.stage("setup_node_extract", ["tar", "-xJf", str(payload), "--strip-components=1", "-C", str(node)], STAGE_TIMEOUT_SECONDS)
        payload.unlink()
        if not reuse:
            self.environment["PATH"] = str(node / "bin") + os.pathsep + self.environment["PATH"]
        binary = Path(self.version(["node", "-p", "process.execPath"]))
        npm_root = binary.parent.parent / "lib/node_modules/npm"
        bin_directory = self.directory / "bin"
        bin_directory.mkdir()
        (bin_directory / "node-gyp").symlink_to(npm_root / "node_modules/node-gyp/bin/node-gyp.js")
        self.environment["PATH"] = str(bin_directory) + os.pathsep + self.environment["PATH"]
        self.environment["npm_config_nodedir"] = str(node)
        self.stage("setup_pnpm", ["npm", "install", "--prefix", str(self.directory / "pnpm"), "--cache", str(self.directory / "tool-npm-cache"), "--no-audit", "--no-fund", "pnpm@" + PNPM_VERSION], 180)
        shutil.rmtree(self.directory / "tool-npm-cache", ignore_errors=True)
        self.environment["PATH"] = str(self.directory / "pnpm/node_modules/.bin") + os.pathsep + self.environment["PATH"]
        if self.version(["node", "--version"]) != NODE_VERSION or self.version(["pnpm", "--version"]) != PNPM_VERSION:
            raise WorkloadFailure()
        for setting, directory in {"storeDir": "store", "cacheDir": "pnpm-cache", "stateDir": "pnpm-state"}.items():
            if Path(self.version(["pnpm", "config", "get", setting])).resolve() != (self.directory / directory).resolve():
                raise WorkloadFailure()
        if self.version(["pnpm", "config", "get", "sideEffectsCache"]) != "false":
            raise WorkloadFailure()
        self.emit("tools", node=NODE_VERSION, pnpm=PNPM_VERSION, image_node_reused=reuse, download_sha256=checksum, headers_version=NODE_VERSION)

    def run(self):
        self.check_source()
        self.setup()
        for path in ["node_modules", "backend/node_modules", ".svelte-kit", "build", "backend/dist"]:
            shutil.rmtree(self.workspace / path, ignore_errors=True)
        self.stage("install", ["pnpm", "install", "--frozen-lockfile", "--store-dir", str(self.directory / "store")])
        self.stage("build_frontend", ["pnpm", "run", "build:frontend"])
        self.stage("unit_tests", ["pnpm", "test"])


def main():
    benchmark = FrontendBenchmark()
    code = 0
    try:
        benchmark.run()
    except WorkloadFailure as error:
        code = error.code
    except (OSError, ValueError, subprocess.SubprocessError):
        code = 1
    finally:
        workload = ["install", "build_frontend", "unit_tests"]
        benchmark.emit("result", exit_code=code, completed=code == 0, measured_stages=list(benchmark.stage_seconds), setup_seconds=round(sum(value for key, value in benchmark.stage_seconds.items() if key.startswith("setup_")), 6), workload_seconds=round(sum(benchmark.stage_seconds.get(key, 0) for key in workload), 6), workload_complete=all(key in benchmark.stage_seconds for key in workload) and code == 0)
    return code


if __name__ == "__main__":
    sys.exit(main())
