"""Fixed, bounded Linux experiments; never dumps environment or command lines."""

import concurrent.futures
import hashlib
import ipaddress
import json
import multiprocessing
import os
import platform
import random
import re
import resource
import shutil
import signal
import ssl
import subprocess
import tempfile
import time
import zlib
from pathlib import Path

NODE_URL = "https://nodejs.org/dist/v24.12.0/node-v24.12.0-linux-x64.tar.xz"
NODE_SHA256 = "bdebee276e58d0ef5448f3d5ac12c67daa963dd5e0a9bb621a53d1cefbc852fd"
REGISTRY_URL = "https://registry.npmjs.org/pnpm/12.4.2"
MAX_SECONDS = 175
SMALL_FILES = 1000
SMALL_FILE_BYTES = 4096
LARGE_BYTES = 32 * 1024**2
LARGE_ROUNDS = 4
RUSAGE_FIELDS = {"user_cpu_seconds": "ru_utime", "system_cpu_seconds": "ru_stime", "minflt": "ru_minflt", "majflt": "ru_majflt", "inblock": "ru_inblock", "oublock": "ru_oublock", "nvcsw": "ru_nvcsw", "nivcsw": "ru_nivcsw"}
IO_KEYS = {"rchar", "wchar", "syscr", "syscw", "read_bytes", "write_bytes", "cancelled_write_bytes"}
CPU_KEYS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal", "guest", "guest_nice")
CGROUP_CPU_KEYS = {"usage_usec", "user_usec", "system_usec", "nr_periods", "nr_throttled", "throttled_usec", "nr_bursts", "burst_usec"}
CGROUP_MEMORY_KEYS = {"low", "high", "max", "oom", "oom_kill", "oom_group_kill"}
STAGES = {"network_npm_metadata_1", "network_npm_metadata_2", "network_npm_metadata_3", "network_node_archive", "tools_pnpm_install", "cpu_sha256_1", "cpu_sha256_4", "cpu_zlib_1", "cpu_zlib_4", "fs_small_workspace", "fs_small_shm", "fs_large_workspace", "pnpm_fetch", "pnpm_offline_no_scripts", "pnpm_offline_hardlink", "pnpm_offline_copy", "pnpm_offline_rayon_1", "pnpm_offline_rayon_4", "pnpm_offline_with_scripts", "pnpm_exec_verify_install", "pnpm_exec_verify_ignore"}


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def counters(path, keys):
    result = {}
    for line in read(path).splitlines():
        fields = line.replace(":", "").split()
        if len(fields) == 2 and fields[0] in keys and fields[1].isdigit():
            result[fields[0]] = int(fields[1])
    return result


def cgroup_path():
    relative = next((line.split("::", 1)[1] for line in read("/proc/self/cgroup").splitlines() if line.startswith("0::")), "")
    path = Path("/sys/fs/cgroup") / relative.lstrip("/")
    return path if path.is_relative_to("/sys/fs/cgroup") else Path("/sys/fs/cgroup")


def cpu_ticks():
    totals = {key: 0 for key in CPU_KEYS}
    wanted = {"cpu" + str(cpu) for cpu in os.sched_getaffinity(0)}
    for line in read("/proc/stat").splitlines():
        fields = line.split()
        if fields and fields[0] in wanted:
            for key, value in zip(CPU_KEYS, fields[1:]):
                totals[key] += int(value)
    return totals


def snapshot():
    return {"self": resource.getrusage(resource.RUSAGE_SELF), "children": resource.getrusage(resource.RUSAGE_CHILDREN), "proc_io": counters("/proc/self/io", IO_KEYS), "cpu_ticks": cpu_ticks(), "cgroup_cpu": counters(cgroup_path() / "cpu.stat", CGROUP_CPU_KEYS), "cgroup_memory_events": counters(cgroup_path() / "memory.events", CGROUP_MEMORY_KEYS)}


def delta(before, after):
    return {key: after[key] - before[key] for key in before.keys() & after.keys()}


def progress_events(lines):
    counts = {key: 0 for key in ["resolved", "fetched", "downloaded", "imported"]}
    methods = {key: 0 for key in ["clone", "hardlink", "copy"]}
    lifecycle = 0
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        status, method = event.get("status"), event.get("method")
        if event.get("name") == "pnpm:progress" and isinstance(status, str) and status in counts:
            counts[status] += 1
        if event.get("name") == "pnpm:progress" and status == "imported" and isinstance(method, str) and method in methods:
            methods[method] += 1
        lifecycle += event.get("name") == "pnpm:lifecycle"
    return {"progress_counts": counts, "lifecycle_events": lifecycle, "import_method_counts": methods}


def resources(before, after):
    result = {who: {key: round(getattr(after[who], attribute) - getattr(before[who], attribute), 6) for key, attribute in RUSAGE_FIELDS.items()} for who in ["self", "children"]}
    result.update({key + "_delta": delta(before[key], after[key]) for key in ["proc_io", "cpu_ticks", "cgroup_cpu", "cgroup_memory_events"]})
    result.update(child_maxrss_kib_before=before["children"].ru_maxrss, child_maxrss_kib_after=after["children"].ru_maxrss)
    return result


def filesystem(path):
    path = Path(path).resolve()
    info = os.statvfs(path)
    match = None
    for line in read("/proc/self/mountinfo").splitlines():
        fields = line.split()
        if "-" not in fields or len(fields) < 7:
            continue
        mount = fields[4].replace("\\040", " ")
        if path.is_relative_to(mount) and (match is None or len(mount) > len(match["mountpoint"])):
            separator = fields.index("-")
            options = fields[5].split(",") + fields[separator + 3].split(",")
            safe = {"rw", "ro", "relatime", "noatime", "strictatime", "nosuid", "nodev", "noexec", "sync", "async", "lazytime", "ssd", "nossd", "space_cache", "space_cache=v2", "nodatacow", "datasum", "nodatasum", "autodefrag", "discard", "discard=async", "nodiscard", "delalloc", "nodelalloc", "barrier", "nobarrier", "data=ordered", "data=journal", "data=writeback"}
            projected = sorted({option for option in options if option in safe or re.fullmatch(r"compress(?:-force)?=(?:zstd|zlib|lzo)(?::[0-9]{1,2})?|(?:commit|metadata_ratio|max_inline)=[0-9]{1,6}", option)})
            match = {"mountpoint": mount, "fstype": fields[separator + 1], "device": fields[2], "options": projected}
    return {**(match or {}), "block_bytes": info.f_frsize, "total_bytes": info.f_blocks * info.f_frsize, "available_bytes": info.f_bavail * info.f_frsize, "files": info.f_files, "files_free": info.f_ffree}


def cpu_worker(payload):
    algorithm, count = payload
    data = b"x" * (4 * 1024**2) if algorithm == "sha256" else random.Random(7).randbytes(1024**2)
    started, cpu = time.perf_counter(), time.process_time()
    if algorithm == "sha256":
        digest = hashlib.sha256()
        for _ in range(count):
            digest.update(data)
        value = digest.hexdigest()
    else:
        for _ in range(count):
            value = hashlib.sha256(zlib.compress(data, 6)).hexdigest()
    return {"wall": time.perf_counter() - started, "cpu": time.process_time() - cpu, "digest": value, "bytes": len(data) * count}


def cpu_experiment(algorithm, workers):
    count = 512 if algorithm == "sha256" else 32
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(cpu_worker, [(algorithm, count)] * workers))
    return {"algorithm": algorithm, "workers": workers, "iterations": count, "bytes_per_worker": results[0]["bytes"], "total_input_bytes": sum(value["bytes"] for value in results), "worker_wall_seconds": [round(value["wall"], 6) for value in results], "worker_cpu_seconds": [round(value["cpu"], 6) for value in results], "digest_match": len({value["digest"] for value in results}) == 1}


def small_files(location, base):
    directory = Path(tempfile.mkdtemp(prefix="yucca-small-", dir=base))
    times = {}
    try:
        operations = {
            "create": lambda: [(directory / str(index)).write_bytes(b"x" * SMALL_FILE_BYTES) for index in range(SMALL_FILES)],
            "stat": lambda: [(directory / str(index)).stat() for index in range(SMALL_FILES)],
            "hardlink": lambda: [os.link(directory / str(index), directory / (str(index) + ".link")) for index in range(SMALL_FILES)],
            "read": lambda: [(directory / str(index)).read_bytes() for index in range(SMALL_FILES)],
            "remove": lambda: [path.unlink() for path in directory.iterdir()],
        }
        for operation, action in operations.items():
            started = time.perf_counter()
            action()
            times[operation] = round(time.perf_counter() - started, 6)
        return {"location": location, "files": SMALL_FILES, "bytes_per_file": SMALL_FILE_BYTES, "total_bytes": SMALL_FILES * SMALL_FILE_BYTES, "operation_seconds": times}
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def large_file(base):
    path = Path(base) / "yucca-large.bin"
    block = random.Random(8).randbytes(1024**2)
    try:
        started = time.perf_counter()
        for _ in range(LARGE_ROUNDS):
            with path.open("wb") as target:
                for _ in range(LARGE_BYTES // len(block)):
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
        write_seconds = time.perf_counter() - started
        started = time.perf_counter()
        advisory = hasattr(os, "posix_fadvise")
        for _ in range(LARGE_ROUNDS):
            with path.open("rb") as target:
                if advisory:
                    os.posix_fadvise(target.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                while target.read(1024**2):
                    pass
        read_seconds = time.perf_counter() - started
        total = LARGE_BYTES * LARGE_ROUNDS
        return {"file_bytes": LARGE_BYTES, "rounds": LARGE_ROUNDS, "bytes_written": total, "bytes_read": total, "write_fsync_seconds": round(write_seconds, 6), "read_seconds": round(read_seconds, 6), "advisory_drop": advisory, "write_mib_per_second": round(total / 1024**2 / write_seconds, 3), "read_mib_per_second": round(total / 1024**2 / read_seconds, 3)}
    finally:
        path.unlink(missing_ok=True)


class ProbeFailure(Exception):
    def __init__(self, code=1):
        self.code = code


class Diagnostics:
    def __init__(self):
        self.started = time.monotonic()
        self.directory = Path(tempfile.mkdtemp(prefix="yucca-diagnostics-", dir=os.environ["RUNNER_TEMP"]))
        self.output = Path(os.environ["RUNNER_TEMP"]) / "yucca-diagnostics.jsonl"
        self.common = {"schema": 1, "backend": os.environ["BENCHMARK_BACKEND"], "pair": int(os.environ["BENCHMARK_PAIR"]), "commit": os.environ["GITHUB_SHA"], "run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"]}
        self.processes = {}
        self.tree_peak = 0

    def emit(self, kind, **fields):
        body = json.dumps({**self.common, "kind": kind, **fields}, sort_keys=True)
        print("YUCCA_DIAGNOSTIC_JSON " + body, flush=True)
        with self.output.open("a") as target:
            target.write(body + "\n")

    def stage(self, name, action):
        assert name in STAGES
        before = snapshot()
        self.processes = {}
        self.tree_peak = 0
        started = time.perf_counter()
        exit_code, details = 0, {}
        try:
            details = action()
        except ProbeFailure as error:
            exit_code = error.code
            raise
        except Exception:
            exit_code = 1
            raise
        finally:
            self.emit("stage", stage=name, wall_seconds=round(time.perf_counter() - started, 6), exit_code=exit_code, details=details, resources={**resources(before, snapshot()), "processes": sorted(self.processes.values(), key=lambda value: value["user_cpu_seconds"] + value["system_cpu_seconds"], reverse=True)[:24], "sampled_tree_peak_rss_bytes": self.tree_peak})
        return details

    def sample_processes(self, root):
        pending, seen, rss = [root], set(), 0
        while pending and len(seen) < 128:
            pid = pending.pop()
            if pid in seen:
                continue
            seen.add(pid)
            text = read(Path("/proc") / str(pid) / "stat")
            if ") " not in text:
                continue
            comm, tail = text.rsplit(") ", 1)
            fields = tail.split()
            if len(fields) < 22:
                continue
            name = comm.split("(", 1)[-1]
            allowed = {"node", "pnpm", "npm", "python3", "make", "g++", "gcc", "cc", "cc1", "cc1plus", "ld", "collect2", "curl", "tar", "xz", "MainThread"}
            resident = max(0, int(fields[21])) * os.sysconf("SC_PAGE_SIZE")
            rss += resident
            previous = self.processes.get(pid, {})
            self.processes[pid] = {"pid": pid, "comm": name if name in allowed else "other", "user_cpu_seconds": int(fields[11]) / os.sysconf("SC_CLK_TCK"), "system_cpu_seconds": int(fields[12]) / os.sysconf("SC_CLK_TCK"), "minflt": int(fields[7]), "majflt": int(fields[9]), "rss_peak_bytes": max(previous.get("rss_peak_bytes", 0), resident), "threads_peak": max(previous.get("threads_peak", 0), int(fields[17])), "sample_count": previous.get("sample_count", 0) + 1, **counters(Path("/proc") / str(pid) / "io", IO_KEYS)}
            pending.extend(int(value) for value in read(Path("/proc") / str(pid) / "task" / str(pid) / "children").split() if value.isdecimal())
        self.tree_peak = max(self.tree_peak, rss)

    def command(self, arguments, timeout=45, extra=None):
        remaining = MAX_SECONDS - (time.monotonic() - self.started)
        if remaining <= 0:
            raise ProbeFailure(124)
        log = self.directory / "command-output"
        with log.open("wb") as output:
            process = subprocess.Popen(arguments, stdout=output, stderr=output, env={**os.environ, **(extra or {})}, start_new_session=True)
            until = time.monotonic() + min(timeout, remaining)
            while True:
                self.sample_processes(process.pid)
                left = until - time.monotonic()
                if left <= 0:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise ProbeFailure(124)
                try:
                    code = process.wait(timeout=min(0.1, left))
                    break
                except subprocess.TimeoutExpired:
                    pass
        if code:
            raise ProbeFailure(code)
        with log.open("rb") as source:
            lines = source.read(2 * 1024**2).decode(errors="replace").splitlines()
        return progress_events(lines)

    def network(self, target, attempt):
        archive = target == "node_archive"
        body = self.directory / ("node.tar.xz" if archive else "metadata.json")
        headers = self.directory / "network-headers"
        result = subprocess.run(["curl", "--silent", "--show-error", "--fail", "--location", "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "5", "--max-time", "20", "--max-filesize", str(64 * 1024**2), "--dump-header", str(headers), "--output", str(body), "--write-out", "%{json}", NODE_URL if archive else REGISTRY_URL], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=25, check=False)
        try:
            raw = json.loads(result.stdout)
        except ValueError:
            raise ProbeFailure() from None
        details = {"target": target, "attempt": attempt, "curl_exit_code": result.returncode, "http_code": int(raw.get("http_code", 0))}
        for name, field in {"dns_seconds": "time_namelookup", "connect_elapsed_seconds": "time_connect", "tls_elapsed_seconds": "time_appconnect", "ttfb_elapsed_seconds": "time_starttransfer", "total_seconds": "time_total", "download_bytes": "size_download", "download_bytes_per_second": "speed_download"}.items():
            value = raw.get(field)
            details[name] = value if isinstance(value, (int, float)) and value >= 0 else None
        try:
            details["remote_ip"] = str(ipaddress.ip_address(raw.get("remote_ip", "")))
        except ValueError:
            details["remote_ip"] = None
        for line in read(headers).splitlines():
            key, _, value = line.partition(":")
            value = value.strip()
            if key.lower() == "cf-ray" and re.fullmatch(r"[a-f0-9]+-[A-Z]{3}", value):
                details["cdn_colo"] = value.rsplit("-", 1)[1]
            if key.lower() == "cf-cache-status" and value in {"HIT", "MISS", "DYNAMIC", "BYPASS", "EXPIRED", "REVALIDATED", "UPDATING", "STALE"}:
                details["cache_status"] = value
        details["checksum_valid"] = hashlib.sha256(body.read_bytes()).hexdigest() == NODE_SHA256 if archive and result.returncode == 0 else None
        if result.returncode or details["http_code"] != 200 or archive and not details["checksum_valid"]:
            raise ProbeFailure()
        return details

    def materialize(self, method, pool_threads, store):
        if method not in {"auto", "hardlink", "copy"} or type(pool_threads) is not int or pool_threads not in {0, 1, 4}:
            raise ProbeFailure()
        extra = {"RAYON_NUM_THREADS": str(pool_threads)} if pool_threads else {}
        result = self.command(["pnpm", "install", "--offline", "--frozen-lockfile", "--ignore-scripts", "--package-import-method=" + method, "--store-dir", str(store), "--reporter=ndjson"], 45, extra)
        return {**result, "variant": "offline-no-scripts" if method == "auto" else "offline-" + method, "requested_import_method": method, "requested_pool_threads": pool_threads}

    def run(self):
        affinity = sorted(os.sched_getaffinity(0))
        if len(affinity) != 4 or os.getuid() == 0:
            raise ProbeFailure()
        for attempt in range(1, 4):
            self.stage("network_npm_metadata_" + str(attempt), lambda attempt=attempt: self.network("npm_metadata", attempt))
        self.stage("network_node_archive", lambda: self.network("node_archive", 1))
        tools = self.directory / "tools"
        (tools / "node").mkdir(parents=True)
        (tools / "bin").mkdir()
        self.command(["tar", "-xJf", str(self.directory / "node.tar.xz"), "--strip-components=1", "-C", str(tools / "node")])
        os.symlink(tools / "node/lib/node_modules/npm/node_modules/node-gyp/bin/node-gyp.js", tools / "bin/node-gyp")
        os.environ["PATH"] = str(tools / "bin") + ":" + str(tools / "node/bin") + ":" + os.environ["PATH"]
        self.stage("tools_pnpm_install", lambda: self.command(["npm", "install", "--prefix", str(tools / "pnpm"), "--cache", str(tools / "npm-cache"), "--no-audit", "--no-fund", "pnpm@12.4.2"]))
        os.environ["PATH"] = str(tools / "pnpm/node_modules/.bin") + ":" + os.environ["PATH"]
        os.environ["npm_config_nodedir"] = str(tools / "node")
        store = self.directory / "store"
        for name, relative in {"XDG_CACHE_HOME": "xdg-cache", "XDG_CONFIG_HOME": "xdg-config", "XDG_DATA_HOME": "xdg-data", "XDG_STATE_HOME": "xdg-state", "PNPM_HOME": "pnpm-home", "PNPM_CONFIG_CACHE_DIR": "pnpm-cache", "PNPM_CONFIG_STATE_DIR": "pnpm-state", "PNPM_CONFIG_STORE_DIR": "store", "npm_config_cache": "npm-cache"}.items():
            os.environ[name] = str(self.directory / relative)
        os.environ["PNPM_CONFIG_SIDE_EFFECTS_CACHE"] = "false"
        node = subprocess.check_output(["node", "--version"], text=True).strip()
        pnpm = subprocess.check_output(["pnpm", "--version"], text=True).strip()
        if node != "v24.12.0" or pnpm != "12.4.2":
            raise ProbeFailure()
        for setting, expected in {"storeDir": store, "cacheDir": self.directory / "pnpm-cache", "stateDir": self.directory / "pnpm-state"}.items():
            if subprocess.check_output(["pnpm", "config", "get", setting], text=True).strip() != str(expected):
                raise ProbeFailure()
        cpuinfo = read("/proc/cpuinfo")
        flags = next((line.split(":", 1)[1].split() for line in cpuinfo.splitlines() if line.startswith("flags")), [])
        memory = next((int(line.split()[1]) * 1024 for line in read("/proc/meminfo").splitlines() if line.startswith("MemTotal:")), None)
        self.emit("environment", node=node, pnpm=pnpm, lock_sha256=hashlib.sha256(Path("pnpm-lock.yaml").read_bytes()).hexdigest(), nominal={"cpu": 4, "memory_gib": 12, "disk_gb": 20} if self.common["backend"] == "cloudflare" else {"cpu": 4, "memory_gb": 16, "disk_gb": 14}, hardware={"affinity": affinity, "cpu_count": os.cpu_count(), "cpu_model": sorted({line.split(":", 1)[1].strip()[:128] for line in cpuinfo.splitlines() if line.startswith("model name")})[:4], "cpu_flags": sorted(flag for flag in flags if re.fullmatch(r"[a-z0-9_]{1,40}", flag))[:256], "python": platform.python_version(), "openssl": ssl.OPENSSL_VERSION, "zlib": zlib.ZLIB_RUNTIME_VERSION, "kernel": platform.release(), "memory_bytes": memory, "cgroup_path": str(cgroup_path()), "cgroup_limits": {name: read(cgroup_path() / name)[:128] for name in ["cpu.max", "memory.max", "memory.swap.max", "cpuset.cpus.effective"]}, "filesystems": {"workspace": filesystem(Path.cwd()), "temp_store": filesystem(self.directory), "shm": filesystem("/dev/shm")}, "ticks_per_second": os.sysconf("SC_CLK_TCK")})
        for algorithm in ["sha256", "zlib"]:
            for workers in [1, 4]:
                self.stage("cpu_" + algorithm + "_" + str(workers), lambda algorithm=algorithm, workers=workers: cpu_experiment(algorithm, workers))
        self.stage("fs_small_workspace", lambda: small_files("workspace", Path.cwd()))
        if os.access("/dev/shm", os.W_OK) and shutil.disk_usage("/dev/shm").free >= 32 * 1024**2:
            self.stage("fs_small_shm", lambda: small_files("shm", "/dev/shm"))
        self.stage("fs_large_workspace", lambda: large_file(Path.cwd()))
        def clean():
            for relative in ["node_modules", "backend/node_modules", ".svelte-kit", "build", "backend/dist"]:
                shutil.rmtree(relative, ignore_errors=True)
        clean()
        self.stage("pnpm_fetch", lambda: {**self.command(["pnpm", "fetch", "--ignore-scripts", "--store-dir", str(store), "--reporter=ndjson"], 60), "variant": "fetch"})
        methods = [("auto", 0, "pnpm_offline_no_scripts"), ("hardlink", 0, "pnpm_offline_hardlink"), ("copy", 0, "pnpm_offline_copy"), ("auto", 1, "pnpm_offline_rayon_1"), ("auto", 4, "pnpm_offline_rayon_4")]
        offset = ((self.common["pair"] - 1) * 2) % len(methods)
        for method, pool_threads, stage in methods[offset:] + methods[:offset]:
            clean()
            self.stage(stage, lambda method=method, pool_threads=pool_threads: self.materialize(method, pool_threads, store))
        clean()
        self.stage("pnpm_offline_with_scripts", lambda: {**self.command(["pnpm", "install", "--offline", "--frozen-lockfile", "--store-dir", str(store), "--reporter=ndjson"], 45), "variant": "offline-with-scripts"})
        for value, name in [("install", "install"), ("false", "ignore")]:
            self.stage("pnpm_exec_verify_" + name, lambda value=value, name=name: {**self.command(["pnpm", "exec", "node", "--version"], 20, {"PNPM_CONFIG_VERIFY_DEPS_BEFORE_RUN": value}), "variant": "verify-" + name, "verify_deps_before_run": value})


if __name__ == "__main__":
    try:
        Diagnostics().run()
    except (ProbeFailure, OSError, ValueError, KeyError, subprocess.SubprocessError, concurrent.futures.BrokenExecutor):
        print('YUCCA_DIAGNOSTIC_ERROR {"error":"Diagnostic failed"}', flush=True)
        raise SystemExit(1) from None
