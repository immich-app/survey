"""Project known actions/runner v2.338.1 startup events without exposing logs.

The raw diagnostic/performance files can contain job credentials. Never copy,
print, upload or return their contents. This module returns fixed event enums
and validated UTC timestamps only; no import-time capture occurs.
"""

import json
import os
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024
PERF_ROOT = Path("/work/state/runner-perf")
REGIONS = {"AFR", "APAC", "EEUR", "ENAM", "WNAM", "ME", "OC", "SAM", "WEUR"}
JOB_RENEWED_EVENT = "job_request_renewed"
PERF_FIXED = {
    "RunnerProcessStarted": "listener_main_started",
    "SessionCreated": "session_created",
    "StartingWorkerProcess": "worker_spawn_started",
    "WorkerProcessStarted": "worker_main_started",
    "WorkerWaitingForJobMessage": "worker_waiting_for_message",
    "MessageReceived_RunnerJobRequest": "job_reference_received",
    "MessageReceived_PipelineAgentJobRequest": "job_message_received",
}
PERF_SUFFIX = {
    "RunnerSendingJobToWorker": "worker_message_send_started",
    "SentJobToWorker": "worker_message_sent",
    "WorkerJobMessageReceived": "worker_message_received",
    "WorkerJobServerQueueStarted": "worker_job_server_queue_started",
    "WorkerJobInitialized": "worker_job_initialized",
}
TRACE_FIXED = {
    ("MessageListener", "Attempt to create session."): "session_attempt_trace",
    ("BrokerMessageListener", "Attempt to create session."): "session_attempt_trace",
    (
        "MessageListener",
        "Connecting to the Runner Server...",
    ): "runner_connect_started_trace",
    (
        "BrokerMessageListener",
        "Connecting to the Broker Server...",
    ): "broker_connect_started_trace",
    (
        "BrokerMessageListener",
        "Connecting to the Runner server...",
    ): "runner_connect_started_trace",
    ("MessageListener", "VssConnection created"): "connection_created_trace",
    ("BrokerMessageListener", "VssConnection created"): "connection_created_trace",
    ("MessageListener", "Session created."): "session_created_trace",
    ("BrokerMessageListener", "Session created."): "session_created_trace",
    (
        "Worker",
        "Waiting to receive the job message from the channel.",
    ): "worker_waiting_for_message_trace",
    ("Worker", "Message received."): "worker_message_received_trace",
    (
        "JobRunner",
        "Starting the job execution context.",
    ): "job_execution_context_started_trace",
    (
        "JobRunner",
        "Initialize job. Getting all job steps.",
    ): "job_initialization_started_trace",
    ("JobRunner", "Run all job steps."): "job_steps_started_trace",
}
TRACE_DYNAMIC = [
    (
        "Listener",
        re.compile(r"Runner is built for [A-Za-z0-9 ()_.-]+\."),
        "listener_first_trace",
    ),
    (
        "JobDispatcher",
        re.compile(
            r"Job request [0-9]+ for plan [0-9a-fA-F-]+ job [0-9a-fA-F-]+ received\."
        ),
        "job_dispatch_received_trace",
    ),
    (
        "JobDispatcher",
        re.compile(r"Send job request message to worker for job [0-9a-fA-F-]+\."),
        "worker_message_send_started_trace",
    ),
]
TRACE_LINE = re.compile(
    r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\dZ) INFO ([A-Za-z.]+)\] (.*)$"
)
ROLES = {
    "Runner.Listener": "listener",
    "Runner.Worker": "worker",
    "ci-supervisor": "supervisor",
}


def utc(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d{1,7})?(?:Z|\+00:00)", value
    ):
        return None
    try:
        return datetime.fromisoformat(value).isoformat()
    except ValueError:
        return None


def parse_perf(raw, source):
    events = []
    for line in raw.splitlines():
        counter, separator, timestamp = line.partition(":")
        event = PERF_FIXED.get(counter)
        if re.fullmatch(r"JobRequestRenewed_[0-9]+", counter):
            event = JOB_RENEWED_EVENT
        if event is None:
            for prefix, candidate in PERF_SUFFIX.items():
                if re.fullmatch(re.escape(prefix) + r"_[0-9a-fA-F-]+", counter):
                    event = candidate
                    break
        value = utc(timestamp) if separator else None
        if event and value:
            events.append(
                {
                    "event": event,
                    "utc": value,
                    "resolution_seconds": 0.000001,
                    "source": source,
                }
            )
    return events


def project_placement(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    location = value.get("location")
    if isinstance(location, str) and re.fullmatch(r"[A-Za-z]{3}[0-9]{0,2}", location):
        result["location"] = location.lower()
    region = value.get("region")
    if isinstance(region, str) and region in REGIONS:
        result["region"] = region
    country = value.get("country_a2")
    if isinstance(country, str) and re.fullmatch(r"[A-Z]{2}", country):
        result["country_a2"] = country
    return result


def parse_trace(raw, source):
    events = []
    for line in raw.splitlines():
        match = TRACE_LINE.fullmatch(line)
        if not match:
            continue
        timestamp, component, message = match.groups()
        event = TRACE_FIXED.get((component, message))
        if event is None:
            event = next(
                (
                    event
                    for trace, pattern, event in TRACE_DYNAMIC
                    if trace == component and pattern.fullmatch(message)
                ),
                None,
            )
        value = utc(timestamp)
        if event and value:
            events.append(
                {
                    "event": event,
                    "utc": value,
                    "resolution_seconds": 1,
                    "source": source,
                }
            )
    return events


def read_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BYTES:
            raise ValueError("Diagnostic file exceeds limits")
        return stream.read(MAX_BYTES).decode("utf-8", errors="replace")


def process_fields(raw):
    end = raw.rfind(")")
    if end < 0:
        raise ValueError("Invalid process stat")
    values = raw[end + 2 :].split()
    return int(values[1]), int(values[19])


def ancestry(proc=Path("/proc")):
    observed = datetime.now(UTC)
    uptime = float((proc / "uptime").read_text().split()[0])
    ticks_per_second = os.sysconf("SC_CLK_TCK")
    boot = observed - timedelta(seconds=uptime)
    pid = os.getpid()
    processes = []
    directories = {}
    for _ in range(24):
        if pid <= 0:
            break
        path = proc / str(pid)
        parent, ticks = process_fields((path / "stat").read_text())
        role = ROLES.get((path / "comm").read_text().strip())
        if role:
            started = boot + timedelta(seconds=ticks / ticks_per_second)
            processes.append(
                {
                    "role": role,
                    "start_since_boot_seconds": ticks / ticks_per_second,
                    "estimated_start_utc": started.isoformat(),
                }
            )
            if role in {"listener", "worker"}:
                executable = (path / "exe").resolve(strict=True)
                if (
                    executable.name
                    == ("Runner.Listener" if role == "listener" else "Runner.Worker")
                    and executable.parent.name == "bin"
                ):
                    directories[role] = (executable.parent.parent / "_diag", started)
        if parent == pid:
            break
        pid = parent
    return {
        "observed_at": observed.isoformat(),
        "uptime_seconds": uptime,
        "clock_ticks_per_second": ticks_per_second,
        "processes": processes,
    }, directories


def unique_log(directory, role, started):
    prefix = "Runner" if role == "listener" else "Worker"
    candidates = []
    for path in directory.glob(prefix + "_*-utc.log"):
        match = re.fullmatch(prefix + r"_(\d{8}-\d{6})-utc\.log", path.name)
        if not match or path.is_symlink():
            continue
        timestamp = datetime.strptime(match[1], "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
        if abs((timestamp - started).total_seconds()) <= 3:
            candidates.append(path)
    return candidates[0] if len(candidates) == 1 else None


def capture():
    metadata, directories = ancestry()
    events, availability = [], {}
    for role, (directory, started) in directories.items():
        path = unique_log(directory, role, started)
        availability[role + "_diag"] = "not_unique_or_missing"
        if path:
            try:
                events.extend(parse_trace(read_regular(path), role + "_diag"))
                availability[role + "_diag"] = "projected"
            except (OSError, ValueError):
                availability[role + "_diag"] = "unavailable"
    for role, name in [("listener", "Runner.perf"), ("worker", "Worker.perf")]:
        try:
            events.extend(parse_perf(read_regular(PERF_ROOT / name), role + "_perf"))
            availability[role + "_perf"] = "projected"
        except (OSError, ValueError):
            availability[role + "_perf"] = "unavailable"
    return {
        "events": sorted(events, key=lambda event: event["utc"]),
        "process_snapshot": metadata,
        "availability": availability,
        "placement": project_placement(
            {
                "location": os.environ.get("CLOUDFLARE_LOCATION"),
                "region": os.environ.get("CLOUDFLARE_REGION"),
                "country_a2": os.environ.get("CLOUDFLARE_COUNTRY_A2"),
            }
        ),
    }


def main():
    common = {
        "schema": 1,
        "kind": "native_snapshot",
        "backend": os.environ.get("BENCHMARK_BACKEND"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "commit": os.environ.get("GITHUB_SHA"),
        "runner_name": os.environ.get("RUNNER_NAME"),
    }
    valid_common = {"schema": 1, "kind": "native_snapshot"}
    try:
        if (
            common["backend"] not in {"cloudflare", "github"}
            or not str(common["run_id"]).isdecimal()
            or not str(common["run_attempt"]).isdecimal()
            or not re.fullmatch(r"[0-9a-f]{40}", str(common["commit"]))
        ):
            raise ValueError("Invalid identity")
        if not isinstance(common["runner_name"], str) or not re.fullmatch(
            r"[A-Za-z0-9 _.:-]{1,128}", common["runner_name"]
        ):
            raise ValueError("Invalid identity")
        valid_common = common
        result = capture()
    except (OSError, ValueError, KeyError, IndexError) as error:
        result = {"status": "capture_unavailable", "error_type": type(error).__name__}
    print(
        "YUCCA_STARTUP_JSON " + json.dumps({**valid_common, **result}, sort_keys=True),
        flush=True,
    )


if __name__ == "__main__":
    main()
