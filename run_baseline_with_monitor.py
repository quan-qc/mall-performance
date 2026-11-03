import argparse
import csv
import json
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent
JMX = ROOT / "testplans" / "mall_admin_baseline.jmx"


def run_command(command, timeout=30):
    return subprocess.check_output(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    ).strip()


def find_mall_pid():
    marker = "com.macro.mall.MallAdminApplication"
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            command_line = " ".join(process.info["cmdline"] or [])
            if "java" in (process.info["name"] or "").lower() and marker in command_line:
                return process.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    raise RuntimeError("MallAdminApplication process not found")


def process_metrics(process, logical_processors):
    try:
        cpu = process.cpu_percent(interval=None) / logical_processors
        working_set_mb = process.memory_info().rss / 1024 / 1024
        return round(cpu, 2), round(working_set_mb, 2)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return "", ""


def monitor(proc, mall_pid, jtl_path, host_path, docker_path, errors):
    try:
        deadline = time.monotonic() + 300
        while not jtl_path.exists():
            if proc.poll() is not None:
                raise RuntimeError("JMeter exited before creating the JTL file")
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for JMeter JTL creation")
            time.sleep(1)

        with host_path.open("w", newline="", encoding="utf-8") as host_file, \
                docker_path.open("w", newline="", encoding="utf-8") as docker_file:
            host_writer = csv.writer(host_file)
            docker_writer = csv.writer(docker_file)
            host_writer.writerow([
                "timestamp", "host_cpu_percent", "available_memory_mb", "mall_pid",
                "mall_cpu_percent", "mall_working_set_mb", "jmeter_pid",
                "jmeter_cpu_percent", "jmeter_working_set_mb",
            ])
            docker_writer.writerow([
                "timestamp", "container", "cpu_percent", "memory_usage", "memory_percent",
            ])

            logical_processors = os.cpu_count() or 1
            mall_process = psutil.Process(mall_pid)
            jmeter_process = psutil.Process(proc.pid)
            psutil.cpu_percent(interval=None)
            mall_process.cpu_percent(interval=None)
            jmeter_process.cpu_percent(interval=None)
            post_samples = 0
            next_docker_sample = 0.0

            while True:
                timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
                host_cpu = psutil.cpu_percent(interval=None)
                available_mb = psutil.virtual_memory().available / 1024 / 1024
                mall_cpu, mall_working_set = process_metrics(mall_process, logical_processors)
                jmeter_cpu, jmeter_working_set = process_metrics(jmeter_process, logical_processors)
                host_writer.writerow([
                    timestamp, round(host_cpu, 2), round(available_mb), mall_pid,
                    mall_cpu, mall_working_set, proc.pid, jmeter_cpu, jmeter_working_set,
                ])

                now = time.monotonic()
                if now >= next_docker_sample:
                    stats = run_command([
                        "docker", "stats", "--no-stream", "--format",
                        "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}",
                        "mysql", "redis",
                    ])
                    for line in stats.splitlines():
                        fields = line.split("|")
                        if len(fields) == 4:
                            docker_writer.writerow([
                                timestamp, fields[0], fields[1].rstrip("%"), fields[2], fields[3].rstrip("%"),
                            ])
                    next_docker_sample = time.monotonic() + 5

                host_file.flush()
                docker_file.flush()

                if proc.poll() is not None:
                    post_samples += 1
                    if post_samples >= 2:
                        break
                time.sleep(2)
    except Exception as exc:
        errors.append(exc)


def validate(jtl_path, report_dir, host_path):
    with jtl_path.open(encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise RuntimeError("JTL contains no samples")
    errors = sum(row["success"] == "false" for row in rows)
    start = min(int(row["timeStamp"]) for row in rows) / 1000
    end = max(int(row["timeStamp"]) + int(row["elapsed"]) for row in rows) / 1000
    if not (report_dir / "index.html").is_file():
        raise RuntimeError("JMeter HTML report is missing")

    with host_path.open(encoding="utf-8") as file:
        host_rows = list(csv.DictReader(file))
    if not host_rows:
        raise RuntimeError("Host monitor CSV contains no samples")
    monitor_start = datetime.fromisoformat(host_rows[0]["timestamp"]).timestamp()
    monitor_end = datetime.fromisoformat(host_rows[-1]["timestamp"]).timestamp()
    in_test_samples = sum(
        start <= datetime.fromisoformat(row["timestamp"]).timestamp() <= end
        for row in host_rows
    )
    covers_start = monitor_start <= start + 5
    covers_end = monitor_end >= end

    return {
        "samples": len(rows),
        "errors": errors,
        "test_start": datetime.fromtimestamp(start).astimezone().isoformat(),
        "test_end": datetime.fromtimestamp(end).astimezone().isoformat(),
        "monitor_start": datetime.fromtimestamp(monitor_start).astimezone().isoformat(),
        "monitor_end": datetime.fromtimestamp(monitor_end).astimezone().isoformat(),
        "monitor_samples": len(host_rows),
        "monitor_in_test_samples": in_test_samples,
        "covers_test_start_within_5s": covers_start,
        "covers_test_end": covers_end,
        "valid": errors == 0 and covers_start and covers_end,
    }


def main():
    parser = argparse.ArgumentParser(description="前台执行 JMeter 并同步采集完整监控")
    default_jmeter_jar = os.environ.get("JMETER_JAR")
    if not default_jmeter_jar and os.environ.get("JMETER_HOME"):
        default_jmeter_jar = Path(os.environ["JMETER_HOME"]) / "bin" / "ApacheJMeter.jar"
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--threads", type=int, default=10)
    parser.add_argument("--ramp-time", type=int, default=10)
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--java", default=os.environ.get("JAVA", "java"))
    parser.add_argument("--jmeter-jar", type=Path, default=default_jmeter_jar)
    args = parser.parse_args()

    if args.jmeter_jar is None:
        parser.error("请通过 --jmeter-jar、JMETER_JAR 或 JMETER_HOME 指定 JMeter")
    jmeter_jar = args.jmeter_jar.expanduser().resolve()
    if not jmeter_jar.is_file():
        parser.error(f"JMeter JAR 不存在: {jmeter_jar}")

    for directory in [ROOT / "results", ROOT / "runtime", ROOT / "reports"]:
        directory.mkdir(parents=True, exist_ok=True)

    jtl_path = ROOT / "results" / f"baseline-{args.run_id}.jtl"
    jmeter_log = ROOT / "runtime" / f"jmeter-baseline-{args.run_id}.log"
    report_dir = ROOT / "reports" / f"baseline-{args.run_id}"
    host_path = ROOT / "results" / f"monitor-host-{args.run_id}.csv"
    docker_path = ROOT / "results" / f"monitor-docker-{args.run_id}.csv"
    for path in [jtl_path, jmeter_log, report_dir, host_path, docker_path]:
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite existing output: {path}")

    mall_pid = find_mall_pid()
    command = [
        args.java, "-Xms256m", "-Xmx1g", "-Dfile.encoding=UTF-8",
        "-jar", str(jmeter_jar), "-n", "-t", str(JMX),
        f"-Jthreads={args.threads}", f"-Jramp_time={args.ramp_time}",
        f"-Jduration={args.duration}", "-l", str(jtl_path),
        "-j", str(jmeter_log), "-e", "-o", str(report_dir),
    ]
    proc = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    monitor_errors = []
    thread = threading.Thread(
        target=monitor,
        args=(proc, mall_pid, jtl_path, host_path, docker_path, monitor_errors),
        daemon=True,
    )
    thread.start()

    try:
        output, _ = proc.communicate(timeout=args.duration + 360)
    except subprocess.TimeoutExpired:
        proc.kill()
        output, _ = proc.communicate()
        raise TimeoutError("JMeter execution timed out")
    finally:
        thread.join(timeout=60)

    print(output)
    if monitor_errors:
        raise monitor_errors[0]
    if proc.returncode != 0:
        raise RuntimeError(f"JMeter exited with code {proc.returncode}")

    summary = validate(jtl_path, report_dir, host_path)
    summary["run_id"] = args.run_id
    summary["mall_pid"] = mall_pid
    summary["jmeter_pid"] = proc.pid
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary["valid"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
