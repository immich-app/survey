"""Plan Survey's fixed Terraform stack; emit metrics without state or raw output."""

import hashlib
import json
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SURVEY_COMMIT = "c1ff09222c1108ce0772b4d0a81559777edebc16"
DEVTOOLS_COMMIT = "8e461d0adbab4662544fc47795aa6ed8c3267ce4"
BACKEND_STATE_MAPPING = "existing_pr19_legacy_pages"
STATE_PROVIDER_LOCK_AUGMENTATION = "existing_pr19_http_3_6_0"
STATE_PROVIDER_LOCK_SOURCE_COMMIT = "12a766c6edf464b9e5ca2f7dc45f6e3e192f30a9"
LEGACY_HTTP_LOCK = 'provider "registry.opentofu.org/hashicorp/http" {\n  version     = "3.6.0"\n  constraints = "3.6.0"\n  hashes = [\n    "h1:0n4RBz9zNw6TTddh5+x7E8L2+qzPXNwKhK4uoZ/DUwE=",\n    "h1:22Ob7lpzMBSqdrCvoFN5EgmhGPHPBovV/9qo0c/Cd+A=",\n    "h1:2IRBvmWOYrq/ooaYYn2i86jZb7iIUvlg0KlmOMfDHoQ=",\n    "h1:5mucXikk4OcW3un3u94QnMx4AB4Wfih+sXeMd5QxSNk=",\n    "h1:5oU7Zm+2gAVGmxqtJ9E8uTudUkYy/DEn/y3IWphdv4k=",\n    "h1:5w0R4b1/VSzpqQF1tXXPr/qmaQLPVRXamOmPKWFcTk4=",\n    "h1:AEVeJr8xGmwad+JUUQ833C3x5d4W+W2szF5DfwxYppw=",\n    "h1:CPHJ+0zQbS/cX1m55Y90jIOgf1jV3ocUUnqsXAh+9Eg=",\n    "h1:JPewnGDOJudNer5+ghqwXoaJkfot3QRq9uiEYvo+JHU=",\n    "h1:QzbluV2vQLxsJYxjpziQCmPndIoJ/UGS4/UHH/GpwUM=",\n    "h1:TjUNbUdqweRBq/ycQ4ixpNkx5qaYwpXEOn9QCpqNZP8=",\n    "h1:XNbcODP60ajj21N/OO7af8bBg1ltIsYkq9egn7BYbiY=",\n    "h1:tgrbgmX7WYQz9G9ncgu7TkpVB+RlLjJA/Rvp9KPlZH8=",\n    "h1:vLxthX/ZWsOZ+aHKbAMqmNKqD0K5f4nJ8ppy0Ioyup0=",\n    "h1:wZOdGBAZkY8OKEPjKz82j1HloAKOmmvtjWyTxM+I110=",\n    "zh:0f719fa5426bc883e9fa6abf7f6498e48025edafbc29015e2f5c028f1cca3b9d",\n    "zh:1b4d7dafefd6c61764b2f9ed6943ceb9a200dee3590d18747e3a5f6b20ce85e0",\n    "zh:1d23a712984866d29f7b07028a4e99c783c71f1a5dddf08bc3d4e7da9d91a1fa",\n    "zh:257d23d58c3bb024b6bc8eb88736eaf912e934ad47c639d0c3c742bddda849a1",\n    "zh:479860e1a5468f5e04013b9364c9496d7ed0804bf9a1acd8e07558d57609993d",\n    "zh:4cb5e681bf599b411b27c4a2c4066a5fb2ed79aaa3a1a3cb5a30002fec062ce9",\n    "zh:4fb35c3f643dae9f3670d719397a415f815a0b95f8ed7bd8a72f27a94ba78092",\n    "zh:59ba40825ab38db5b4a0989a2db0df35cc15d8984f898176011ba352f27d77b7",\n    "zh:61fc1252eb88088638f4c69ea4e2171cde2e5089fa632ac1e943b13787348f73",\n    "zh:7c5d6dd5f7cbc460e95d368be35c29b4e0402069b8912dbd5d1cd7fa9acef216",\n    "zh:7f76d756240d4284642f359ad470226e5378670239aadc366ef54d9d914d4d2e",\n    "zh:8133ad0814098177e0d067c816ccf1bf48bbadacd18f6f2c808c90447505723b",\n    "zh:c93be06269bb728f1968f8c50506de56c887017ac1d6e4be1f925651d8437eb6",\n    "zh:ef47b78a10a82e6cf53344a6a85a94041c28286c10a70541c564d762f1cfede0",\n    "zh:f5796a53a74999135bd9087aff50fddda59129d09b2f9b1902ff8c0c1e047e48",\n  ]\n}\n'
LEGACY_HTTP_LOCK_SHA256 = "e4a186e70477208a008163f14768e0426c8e46fdd358d20885be8afdb31079c0"
SHAPES = {"lite", "basic", "standard-1", "standard-2", "standard-3", "standard-4", "github"}
MODULES = ("modules/cloudflare/pages-project", "modules/cloudflare/static-pages", "modules/cloudflare/workers/survey")
MODULE_FILES = ("pages-project/pages.tf", "static-pages/pages.tf", "workers/survey/worker.tf")
MARKS = {"checkout_complete", "tools_start", "tools_complete", "assets_ready"}
COMMAND_TIMEOUT_SECONDS = 600
SAMPLE_INTERVAL_SECONDS = 1.0
READ_LIMIT_BYTES = 1024 * 1024
CPU_COUNTERS = {"usage_usec", "user_usec", "system_usec", "nr_periods", "nr_throttled", "throttled_usec", "nr_bursts", "burst_usec"}
MEMORY_COUNTERS = {"low", "high", "max", "oom", "oom_kill", "oom_group_kill"}
RUSAGE_FIELDS = {"user_cpu_seconds": "ru_utime", "system_cpu_seconds": "ru_stime", "minor_faults": "ru_minflt", "major_faults": "ru_majflt", "voluntary_context_switches": "ru_nvcsw", "involuntary_context_switches": "ru_nivcsw", "input_blocks": "ru_inblock", "output_blocks": "ru_oublock"}
PUBLIC_DIRECTORY = "yucca-terraform-public"
PRIVATE_PREFIX = "yucca-terraform-private-"
STAGE_NAMES = {"init", "plan"}
FAILURES = {"success", "success_changes", "timeout", "memory_limit", "killed", "secret_resolution", "authentication", "backend_read_only_or_missing", "network_or_backend_connection", "provider_download", "provider_startup", "configuration", "command_failed", "setup_failed", "postgres_configuration"}
SQLSTATES = {"25006", "42P01", "3F000", "3D000", "28P01", "28000", "42501", "08001", "08006"}
PUBLIC_PROVIDERS = {"cloudflare/cloudflare", "hashicorp/random", "hashicorp/null", "hashicorp/archive", "hashicorp/local", "hashicorp/http", "hashicorp/external", "hashicorp/time", "hashicorp/tls", "hashicorp/dns", "integrations/github", "builtin/terraform"}
PROVIDER_REGISTRIES = {"registry.opentofu.org", "registry.terraform.io", "terraform.io"}
DIAGNOSTIC_PATTERNS = {
    "init_dependency_lock_changed": r"provider dependency changes detected",
    "init_dependency_lock_inconsistent": r"inconsistent dependency lock file",
    "init_provider_checksum": r"checksum|checksums",
    "init_legacy_provider_address": r"invalid legacy provider address",
    "init_backend_config": r"error configuring the backend|backend initialization required|backend configuration changed|failed to load backend",
    "init_state_loading": r"error loading state|error refreshing state|error selecting workspace|failed to refresh state",
    "init_state_version": r"state snapshot was created by|unsupported state file format|state format version|newer than the current",
    "init_provider_cache": r"plugin cache directory|shared cache directory|shared cache",
    "init_flags": r"failed to parse command.line flags|flag provided but not defined",
    "init_configuration": r"invalid expression|missing required|invalid function argument|variables not allowed|unknown variable",
    "init_providers_reached": r"initializing provider plugins",
    "init_success_present": r"successfully initialized",
    "error_marker": r"\berror\b",
    "postgres_readonly": r"read-only transaction",
    "postgres_missing_relation": r'relation "[^"\n]+" does not exist',
    "postgres_missing_schema": r'schema "[^"\n]+" does not exist',
    "postgres_missing_database": r'database "[^"\n]+" does not exist',
    "postgres_missing_role": r'role "[^"\n]+" does not exist',
    "postgres_authentication": r"password authentication failed",
    "postgres_ssl": r"ssl is not enabled|no pg_hba.conf entry",
    "filesystem_missing_file": r"no such file or directory|failed to read file",
    "workspace_missing": r"no stored state was found|workspace[^\n]*does not exist",
    "provider_installation": r"failed to install provider|failed to query available provider packages",
    "module_download": r"error downloading modules|failed to download module|failed to get existing workspaces",
    "unsupported_argument": r"unsupported argument|unsupported block type|unrecognized flag",
    "op_secret_lookup": r"unable to resolve secret|could not find item|not currently signed in",
    "backend_initialization": r"error initializing backend|error inspecting states in the|failed to get existing workspaces|failed to load state|failed to configure",
    "readonly_create": r"cannot execute create [^\n]*read-only transaction",
    "readonly_insert": r"cannot execute insert [^\n]*read-only transaction",
    "readonly_update": r"cannot execute update [^\n]*read-only transaction",
    "readonly_delete": r"cannot execute delete [^\n]*read-only transaction",
    "plan_invalid_attribute": r"unsupported attribute|attempt to get attribute|object does not have an attribute|invalid index|missing resource instance key",
    "plan_undeclared_reference": r"reference to undeclared|undeclared resource|undeclared input variable|undeclared local value|unknown variable|unsupported checkable object kind",
    "plan_invalid_value": r"invalid value for|invalid function argument|invalid template interpolation|invalid operand|incorrect attribute value type|inconsistent conditional result types|invalid for_each argument|invalid count argument",
    "plan_missing_argument": r"missing required argument|required attribute|required argument|no value for required variable",
    "plan_provider_configuration": r"invalid provider configuration|provider configuration not present|provider configuration is required|invalid api token|invalid credentials",
    "plan_provider_schema": r"failed to load plugin schemas|failed to obtain provider schema|failed to decode resource from state|unsupported attribute in state|resource instance managed by newer provider version|no schema available|no resource schema|failed to marshal state|failed to upgrade resource state|invalid resource type|unsupported resource type",
    "plan_provider_inconsistent": r"provider produced inconsistent|invalid address to set|invalid target address",
    "plan_api_request": r"error making request|failed to make|error sending request|error reading|error fetching|error getting|failed to fetch|failed to read|unexpected http|api error|status code|bad request|request failed",
    "plan_api_authentication": r"forbidden|unauthorized|authentication error|permission denied|insufficient permissions|invalid access token|invalid token|403 forbidden|401 unauthorized",
    "plan_http_401": r"(?:status(?: code)?[\s:=]+|http[/0-9.]*[\s:=]+)401\b|401 unauthorized",
    "plan_http_403": r"(?:status(?: code)?[\s:=]+|http[/0-9.]*[\s:=]+)403\b|403 forbidden",
    "plan_http_404": r"(?:status(?: code)?[\s:=]+|http[/0-9.]*[\s:=]+)404\b|404 not found",
    "plan_data_http": r"error making request|error making http request|unexpected response status|failed to read http|request status",
    "plan_api_missing": r"404 not found|not found|does not exist|could not find|cannot find",
    "plan_external_command": r"external program execution failed|error running command|error running provisioner|local-exec provisioner error",
    "plan_remote_state_output": r"unable to find remote state|unsupported attribute|outputs is object with no attributes|no stored state",
    "plan_state_lock": r"error acquiring the state lock|state blob is already locked|failed to save state|error saving state|failed to persist state",
    "plan_invalid_json": r"invalid character|unexpected end of json|json: cannot unmarshal|error decoding response|error parsing response",

}


class BenchmarkFailure(Exception):
    pass


def read(path):
    try:
        with Path(path).open("rb") as source:
            return source.read(READ_LIMIT_BYTES).decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def integer(value):
    return int(value) if isinstance(value, str) and value.isdecimal() and len(value) < 21 else None


def counters(path, allowed):
    result = {}
    for line in read(path).splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] in allowed and integer(fields[1]) is not None:
            result[fields[0]] = int(fields[1])
    return result


def delta(before, after):
    return {key: after[key] - before[key] for key in before.keys() & after.keys() if after[key] >= before[key]}


def cgroup_path(root=Path("/sys/fs/cgroup"), membership=None):
    membership = read("/proc/self/cgroup") if membership is None else membership
    relative = next((line.split("::", 1)[1] for line in membership.splitlines() if line.startswith("0::")), "")
    candidate = (root / relative.lstrip("/")).resolve()
    if not candidate.is_relative_to(root.resolve()):
        return None
    while candidate.is_relative_to(root.resolve()):
        if (candidate / "memory.current").is_file():
            return candidate
        if candidate == root.resolve():
            break
        candidate = candidate.parent
    return None


def cgroup_snapshot(scope):
    if scope is None:
        return {"cpu": {}, "memory_events": {}, "memory_current_bytes": None, "memory_lifetime_peak_bytes": None}
    return {"cpu": counters(scope / "cpu.stat", CPU_COUNTERS), "memory_events": counters(scope / "memory.events", MEMORY_COUNTERS), "memory_current_bytes": integer(read(scope / "memory.current")), "memory_lifetime_peak_bytes": integer(read(scope / "memory.peak"))}


def filesystem(path, mounts=None):
    path = Path(path).resolve(strict=True)
    mounts = read("/proc/self/mountinfo") if mounts is None else mounts
    selected = None
    for line in mounts.splitlines():
        fields = line.split()
        if len(fields) < 10 or "-" not in fields:
            continue
        separator = fields.index("-")
        if separator + 3 >= len(fields):
            continue
        mount = Path(fields[4].replace("\\040", " "))
        if not path.is_relative_to(mount) or (selected is not None and len(str(mount)) <= selected[0]):
            continue
        kind = fields[separator + 1]
        if kind not in {"btrfs", "ext4", "xfs", "overlay", "tmpfs", "zfs"}:
            kind = "other"
        allowed = {"ro", "rw", "nosuid", "nodev", "noexec", "relatime", "noatime", "nodatacow", "nodatasum"}
        options = fields[5].split(",") + fields[separator + 3].split(",")
        selected = (len(str(mount)), {"filesystem": kind, "options": sorted({value for value in options if value in allowed or re.fullmatch(r"compress(?:-force)?=(?:zstd|zlib|lzo)(?::[0-9]{1,2})?", value)})})
    metadata = os.statvfs(path)
    return {**(selected[1] if selected is not None else {"filesystem": "unknown", "options": []}), "total_bytes": metadata.f_blocks * metadata.f_frsize, "available_bytes": metadata.f_bavail * metadata.f_frsize, "block_bytes": metadata.f_frsize}


def host_resources(workspace, scope):
    quota = read(scope / "cpu.max").split() if scope is not None else []
    quota_us = integer(quota[0]) if len(quota) == 2 else None
    period_us = integer(quota[1]) if len(quota) == 2 else None
    memory = next((line.split()[1] for line in read("/proc/meminfo").splitlines() if line.startswith("MemTotal:") and len(line.split()) == 3), "")
    return {"cpu_affinity_count": len(os.sched_getaffinity(0)), "cpu_quota_usec": quota_us, "cpu_period_usec": period_us, "cpu_quota_cores": round(quota_us / period_us, 6) if quota_us is not None and period_us else None, "memory_limit_bytes": integer(read(scope / "memory.max")) if scope is not None else None, "memtotal_bytes": int(memory) * 1024 if memory.isdecimal() else None, "cgroup_version": 2 if scope is not None else None, "workspace": filesystem(workspace), "runner_temp": filesystem(os.environ["RUNNER_TEMP"])}


def process_group_sample(group, proc=Path("/proc")):
    rss, count = 0, 0
    for path in proc.iterdir():
        if not path.name.isdecimal():
            continue
        stat = read(path / "stat")
        ending = stat.rfind(")")
        fields = stat[ending + 2:].split() if ending >= 0 else []
        if len(fields) >= 22 and integer(fields[2]) == group and integer(fields[21]) is not None:
            rss += int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
            count += 1
    return {"rss_bytes": rss, "process_count": count}


def terraform_command(stage):
    if stage not in STAGE_NAMES:
        raise BenchmarkFailure()
    result = ["terragrunt", "run", "--all", stage, "--non-interactive", "--parallelism", "1", *["--queue-include-dir=" + module for module in MODULES]]
    if stage == "plan":
        return [*result, "--no-auto-init", "--", "-lock=false", "-input=false", "-no-color", "-detailed-exitcode"]
    return [*result, "--", "-input=false", "-no-color", "-lockfile=readonly"]


def harden_postgres_uri(value):
    parsed = urlsplit(value)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.netloc or parsed.fragment:
        raise BenchmarkFailure()
    query = parse_qsl(parsed.query, keep_blank_values=True)
    options = " ".join(item for key, item in query if key == "options")
    query = [(key, item) for key, item in query if key != "options"]
    query.append(("options", options + " -c default_transaction_read_only=on"))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def execute(stage):
    environment = dict(os.environ)
    environment["TF_VAR_tf_state_postgres_conn_str"] = harden_postgres_uri(environment["TF_VAR_tf_state_postgres_conn_str"])
    environment.update({"PG_SKIP_SCHEMA_CREATION": "true", "PG_SKIP_TABLE_CREATION": "true", "PG_SKIP_INDEX_CREATION": "true", "PGOPTIONS": "-c default_transaction_read_only=on"})
    for key in ["TF_LOG", "TF_LOG_PATH", "TOFU_LOG", "TOFU_LOG_PATH", "TG_INPUTS_DEBUG"]:
        environment.pop(key, None)
    os.execvpe("terragrunt", terraform_command(stage), environment)


def provider_metadata(value):
    result = {"providers": [], "registry_hosts": [], "unknown_provider_count": 0}
    if not isinstance(value, dict) or not isinstance(value.get("providers"), list):
        return result
    for item in value["providers"][:32]:
        match = re.fullmatch(r'(?:module\.[a-zA-Z0-9_.]+\.)?provider\["([a-z.]+)/([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)"\](?:\.[a-zA-Z0-9_]+)?', item) if isinstance(item, str) else None
        if match and match[1] in PROVIDER_REGISTRIES and match[2] in PUBLIC_PROVIDERS:
            result["providers"].append(match[2])
            result["registry_hosts"].append(match[1])
        else:
            result["unknown_provider_count"] += 1
    result["providers"] = sorted(set(result["providers"]))
    result["registry_hosts"] = sorted(set(result["registry_hosts"]))
    if isinstance(value.get("version"), str) and re.fullmatch(r"[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,3}", value["version"]):
        result["state_version"] = value["version"]
    return result


def state_provider_metadata(environment):
    result = {"available": True, "units": {}}
    for unit, module in zip(["pages_project", "static_pages", "worker"], MODULES, strict=True):
        command = ["terragrunt", "run", "providers", "--non-interactive", "--", "-no-color"]
        with tempfile.TemporaryFile(mode="w+b") as output:
            try:
                value = subprocess.run(command, cwd=Path(module), env=environment, stdout=output, stderr=subprocess.STDOUT, timeout=45, check=False)
            except subprocess.TimeoutExpired:
                result["units"][unit] = {"return_code": 124}
                continue
            output.seek(0)
            text = output.read(READ_LIMIT_BYTES).decode("utf-8", errors="replace")
        addresses = re.findall(r'provider\[([a-z.]+/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)\]', text)
        metadata = provider_metadata({"providers": ['provider["' + address + '"]' for address in addresses]})
        result["units"][unit] = {"return_code": value.returncode, **metadata}
    return result


def project_state_metadata(value):
    if not isinstance(value, dict) or not isinstance(value.get("available"), bool):
        return {}
    result = {"available": value["available"]}
    if not isinstance(value.get("units"), dict):
        return result
    result["units"] = {}
    for unit in ["pages_project", "static_pages", "worker"]:
        source = value["units"].get(unit)
        if not isinstance(source, dict):
            continue
        target = {}
        for key, allowed in [("providers", PUBLIC_PROVIDERS), ("registry_hosts", PROVIDER_REGISTRIES)]:
            if isinstance(source.get(key), list):
                target[key] = sorted({item for item in source[key][:32] if isinstance(item, str) and item in allowed})
        for key, minimum, maximum in [("return_code", -255, 255), ("unknown_provider_count", 0, 32)]:
            number = source.get(key)
            if type(number) is int and minimum <= number <= maximum:
                target[key] = number
        if isinstance(source.get("state_version"), str) and re.fullmatch(r"[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,3}", source["state_version"]):
            target["state_version"] = source["state_version"]
        result["units"][unit] = target
    return result


def classify(return_code, text, timed_out=False, oom_kills=0):
    if timed_out:
        return "timeout"
    if return_code in {0, 2}:
        return "success_changes" if return_code == 2 else "success"
    if oom_kills > 0:
        return "memory_limit"
    if return_code < 0:
        return "killed"
    text = text.lower()
    patterns = [
        ("postgres_configuration", ["yucca_terraform_failure postgres_configuration"]),
        ("secret_resolution", ["not currently signed in", "service account token", "could not find item", "unable to resolve secret"]),
        ("authentication", ["unauthorized", "authentication failed", "permission denied", "status code: 403", "status code: 401"]),
        ("backend_read_only_or_missing", ["read-only transaction", "does not exist", "error loading state", "failed to get existing workspaces"]),
        ("network_or_backend_connection", ["connection refused", "no route to host", "i/o timeout", "dial tcp", "connection timed out"]),
        ("provider_download", ["failed to query available provider packages", "failed to install provider"]),
        ("provider_startup", ["plugin did not respond", "timeout while waiting for plugin", "unrecognized remote plugin message"]),
        ("configuration", ["unsupported argument", "no value for required variable", "invalid function argument", "failed to read file"]),
    ]
    return next((name for name, needles in patterns if any(needle in text for needle in needles)), "command_failed")


def diagnostic_codes(text):
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    result = {"sqlstates": sorted({code for code in re.findall(r"\bSQLSTATE[\s:(]+([A-Z0-9]{5})\b", text, flags=re.IGNORECASE) if code in SQLSTATES}), "codes": sorted(name for name, pattern in DIAGNOSTIC_PATTERNS.items() if re.search(pattern, text, flags=re.IGNORECASE)), "modules": sorted(module for module in MODULES if module in text)}
    for line in text.splitlines():
        if line.startswith("YUCCA_TERRAFORM_STATE_METADATA_JSON "):
            try:
                result["state_provider_metadata"] = project_state_metadata(json.loads(line.split(" ", 1)[1]))
            except ValueError:
                pass
    return result


def measure(command, cwd, environment, scope, timeout=COMMAND_TIMEOUT_SECONDS):
    before, usage_before = cgroup_snapshot(scope), resource.getrusage(resource.RUSAGE_CHILDREN)
    observer_before = resource.getrusage(resource.RUSAGE_SELF)
    started, timed_out, group_peak, process_peak, cgroup_peak, samples = time.monotonic(), False, 0, 0, 0, 0
    with tempfile.TemporaryFile(mode="w+b", dir=cwd) as output:
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        while True:
            sample = process_group_sample(process.pid)
            group_peak = max(group_peak, sample["rss_bytes"])
            process_peak = max(process_peak, sample["process_count"])
            current = cgroup_snapshot(scope)["memory_current_bytes"]
            cgroup_peak = max(cgroup_peak, current or 0)
            samples += 1
            if process.poll() is not None:
                break
            if time.monotonic() - started >= timeout:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                break
            time.sleep(SAMPLE_INTERVAL_SECONDS)
        wall = time.monotonic() - started
        after, usage_after = cgroup_snapshot(scope), resource.getrusage(resource.RUSAGE_CHILDREN)
        observer_after = resource.getrusage(resource.RUSAGE_SELF)
        output_bytes = output.tell()
        output.seek(max(0, output_bytes - READ_LIMIT_BYTES))
        private_text = output.read(READ_LIMIT_BYTES).decode("utf-8", errors="replace")
    memory_events = delta(before["memory_events"], after["memory_events"])
    return {"wall_seconds": round(wall, 6), "return_code": process.returncode, "classification": classify(process.returncode, private_text, timed_out, memory_events.get("oom_kill", 0)), "diagnostics": diagnostic_codes(private_text), "private_output_bytes": output_bytes, "private_output_truncated": output_bytes > READ_LIMIT_BYTES, "child_usage": {key: round(getattr(usage_after, attribute) - getattr(usage_before, attribute), 6) for key, attribute in RUSAGE_FIELDS.items()}, "observer_user_cpu_seconds": round(observer_after.ru_utime - observer_before.ru_utime, 6), "observer_system_cpu_seconds": round(observer_after.ru_stime - observer_before.ru_stime, 6), "largest_child_maxrss_kib_before": usage_before.ru_maxrss, "largest_child_maxrss_kib_after": usage_after.ru_maxrss, "sampled_process_group_peak_rss_bytes": group_peak, "sampled_process_group_peak_count": process_peak, "sampled_cgroup_current_peak_bytes": cgroup_peak, "cgroup_lifetime_peak_before_bytes": before["memory_lifetime_peak_bytes"], "cgroup_lifetime_peak_after_bytes": after["memory_lifetime_peak_bytes"], "cgroup_cpu_delta": delta(before["cpu"], after["cpu"]), "cgroup_memory_events_delta": memory_events, "sample_count": samples, "sample_interval_seconds": SAMPLE_INTERVAL_SECONDS}


def identity(environment):
    shape, trial = environment["BENCHMARK_SHAPE"], environment["BENCHMARK_TRIAL"]
    if shape not in SHAPES or trial not in {"1", "2", "3"} or not re.fullmatch(r"[0-9a-f]{40}", environment["GITHUB_SHA"]):
        raise BenchmarkFailure()
    if any(not environment[key].isdecimal() or len(environment[key]) > 20 for key in ["GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"]):
        raise BenchmarkFailure()
    return {"schema": 1, "kind": "terraform_plan", "shape": shape, "trial": int(trial), "workflow_commit": environment["GITHUB_SHA"], "survey_commit": SURVEY_COMMIT, "devtools_commit": DEVTOOLS_COMMIT, "run_id": environment["GITHUB_RUN_ID"], "run_attempt": int(environment["GITHUB_RUN_ATTEMPT"])}


def public_path():
    directory = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True) / PUBLIC_DIRECTORY
    if directory.is_symlink() or not directory.is_dir():
        raise BenchmarkFailure()
    return directory


def mark(name):
    if name not in MARKS:
        raise BenchmarkFailure()
    path = public_path() / "milestones.json"
    record = json.loads(path.read_text())
    if not isinstance(record, dict) or any(key not in {"first_step_at", *MARKS} or not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00", value) is None for key, value in record.items()) or name in record:
        raise BenchmarkFailure()
    record[name] = datetime.now(UTC).isoformat(timespec="microseconds")
    path.write_text(json.dumps(record, sort_keys=True) + "\n")


def prepare_copy(source, target):
    shutil.copytree(source / "deployment", target / "deployment", ignore=shutil.ignore_patterns(".terraform", ".terragrunt-cache", "*.tfstate", "*.tfstate.backup", "*.tfplan"))
    for relative in MODULE_FILES:
        path = target / "deployment/modules/cloudflare" / relative
        text = path.read_text()
        old = "git::https://github.com/immich-app/devtools.git//"
        pattern = re.escape(old) + r'([^"\s?]+)\?ref=main'
        text, count = re.subn(pattern, lambda match, prefix=old: prefix + match[1] + "?ref=" + DEVTOOLS_COMMIT, text)
        if count != 1:
            raise BenchmarkFailure()
        path.write_text(text)


def map_existing_dev_state(deployment):
    replacements = {
        "modules/cloudflare/pages-project/terragrunt.hcl": ("cloudflare_pages_project_futo_tech_", "cloudflare_pages_project_futo_org_"),
        "modules/cloudflare/static-pages/terragrunt.hcl": ("cloudflare_futo_tech_", "cloudflare_futo_org_"),
        "modules/cloudflare/static-pages/remote-state.tf": ("cloudflare_pages_project_futo_tech_", "cloudflare_pages_project_futo_org_"),
    }
    changes = []
    for relative, (old, new) in replacements.items():
        path = deployment / relative
        text = path.read_text()
        if text.count(old) != 1:
            raise BenchmarkFailure()
        changes.append((path, text.replace(old, new)))
    for path, text in changes:
        path.write_text(text)


def augment_existing_state_lock(deployment):
    path = deployment / "modules/cloudflare/static-pages/.terraform.lock.hcl"
    original = path.read_text()
    if "hashicorp/http" in original or hashlib.sha256(LEGACY_HTTP_LOCK.encode()).hexdigest() != LEGACY_HTTP_LOCK_SHA256:
        raise BenchmarkFailure()
    path.write_text(original.rstrip() + "\n\n" + LEGACY_HTTP_LOCK)



def resolve_tools(workspace):
    binaries = {}
    for command in ["tofu", "terragrunt"]:
        raw = subprocess.check_output(["mise", "which", command], cwd=workspace, stderr=subprocess.DEVNULL, timeout=30).decode().strip()
        path = Path(raw)
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise BenchmarkFailure()
        binaries[command] = path
    versions = {"tofu": "1.12.6", "terragrunt": "1.1.5", "op": "2.39.0"}
    for name, expected in versions.items():
        command = [str(binaries[name]), "version"] if name in binaries else [name, "--version"]
        raw = subprocess.check_output(command, cwd=workspace, stderr=subprocess.DEVNULL, timeout=30).decode()
        if re.search(r"(?<![0-9.])v?" + re.escape(expected) + r"(?![0-9.])", raw) is None:
            raise BenchmarkFailure()
    return versions, os.pathsep.join([*[str(path.parent) for path in binaries.values()], os.environ["PATH"]])


def benchmark():
    result = {**identity(os.environ), "backend_state_mapping": BACKEND_STATE_MAPPING, "state_provider_lock_augmentation": STATE_PROVIDER_LOCK_AUGMENTATION, "state_provider_lock_source_commit": STATE_PROVIDER_LOCK_SOURCE_COMMIT, "success": False, "classification": "setup_failed", "stages": {}}
    output = public_path() / "terraform-plan.json"
    try:
        workspace = Path(os.environ["GITHUB_WORKSPACE"]) / "survey"
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=workspace, stderr=subprocess.DEVNULL, timeout=10).decode().strip()
        if revision != SURVEY_COMMIT or not os.environ.get("OP_SERVICE_ACCOUNT_TOKEN"):
            raise BenchmarkFailure()
        result["assets_sha256"] = {name: hashlib.sha256((workspace / "backend/dist" / name).read_bytes()).hexdigest() for name in ["index.js", "sessions.js"]}
        scope = cgroup_path()
        result["resources"] = host_resources(workspace, scope)
        if result["shape"] != "github" and result["resources"]["workspace"]["filesystem"] == "tmpfs":
            raise BenchmarkFailure()
        result["tool_versions"], tool_path = resolve_tools(workspace)
        with tempfile.TemporaryDirectory(prefix=PRIVATE_PREFIX, dir=os.environ["RUNNER_TEMP"]) as raw:
            scratch = Path(raw)
            prepare_copy(workspace, scratch)
            map_existing_dev_state(scratch / "deployment")
            augment_existing_state_lock(scratch / "deployment")
            environment = {**os.environ, "PATH": tool_path, "ENVIRONMENT": "dev", "TF_VAR_app_name": "survey", "TF_VAR_stage": "pr-19", "TF_VAR_dist_dir": str(workspace / "backend/dist"), "TF_VAR_migrations_dir": str(workspace / "backend/migrations"), "TF_IN_AUTOMATION": "1", "TF_INPUT": "0", "CHECKPOINT_DISABLE": "1", "TG_LOG_LEVEL": "warn", "NO_COLOR": "1", "OP_ACCOUNT": "immich.1password.com", "XDG_CACHE_HOME": str(scratch / "cache"), "TF_PLUGIN_CACHE_DIR": str(scratch / "provider-cache")}
            Path(environment["TF_PLUGIN_CACHE_DIR"]).mkdir(mode=0o700)
            for stage in ["init", "plan"]:
                command = ["op", "run", "--env-file=" + str(scratch / "deployment/.env"), "--", sys.executable, str(Path(__file__).resolve()), "execute", stage]
                value = measure(command, scratch / "deployment", environment, scope)
                result["stages"][stage] = value
                result["classification"] = value["classification"]
                if value["classification"] not in {"success", "success_changes"}:
                    break
            result["success"] = "plan" in result["stages"] and result["classification"] in {"success", "success_changes"}
        result["private_files_removed"] = True
    except (BenchmarkFailure, OSError, ValueError, KeyError, subprocess.SubprocessError):
        result["classification"] = "setup_failed"
    if result["classification"] not in FAILURES:
        raise BenchmarkFailure()
    output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print("YUCCA_TERRAFORM_JSON " + json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["success"] else 1


def main(arguments):
    if len(arguments) == 2 and arguments[0] == "mark":
        mark(arguments[1])
        return 0
    if len(arguments) == 2 and arguments[0] == "execute" and arguments[1] in STAGE_NAMES:
        try:
            execute(arguments[1])
        except (BenchmarkFailure, OSError, ValueError, KeyError):
            print("YUCCA_TERRAFORM_FAILURE postgres_configuration", file=sys.stderr)
            return 1
    if arguments == ["run"]:
        return benchmark()
    raise BenchmarkFailure()


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (BenchmarkFailure, OSError, ValueError, KeyError):
        print("YUCCA_TERRAFORM_FAILURE setup_failed", flush=True)
        raise SystemExit(1) from None
