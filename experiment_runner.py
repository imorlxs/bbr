#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import json
import re
import subprocess
import threading
import time
from pathlib import Path

ALGORITHMS = ("reno", "cubic", "bbr")
PROFILES = {
    "wired": "tc qdisc replace dev eth0 root netem rate 100mbit delay 20ms loss 0%",
    "wireless": "tc qdisc replace dev eth0 root netem rate 20mbit delay 50ms 0.5ms loss 2%",
}


def run(cmd, check=True, capture=True):
    return subprocess.run(cmd, check=check, text=True, capture_output=capture)


def compose_exec(container, command, check=True):
    return run(["docker", "compose", "exec", "-T", container, "bash", "-lc", command], check=check)


def clear_tc(container):
    compose_exec(container, "tc qdisc del dev eth0 root", check=False)


def apply_profile(container, profile):
    clear_tc(container)
    compose_exec(container, PROFILES[profile])


def set_algorithm(container, algorithm):
    result = compose_exec(container, f"sysctl -w net.ipv4.tcp_congestion_control={algorithm}", check=False)
    if result.returncode != 0:
        print(
            f"WARNING: could not set {algorithm} with sysctl in {container}; "
            "continuing with iperf3 -C per-socket setting."
        )


def parse_metrics(text):
    cwnd = re.search(r"cwnd:(\d+)", text)
    rtt = re.search(r"rtt:([0-9.]+)/", text)
    pacing = re.search(r"pacing_rate\s+([^\s]+)", text)
    return (
        cwnd.group(1) if cwnd else "",
        rtt.group(1) if rtt else "",
        pacing.group(1) if pacing else "",
    )


def telemetry_loop(container, csv_path, stop_event):
    with csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_utc", "container", "cwnd", "rtt_ms", "pacing_rate", "raw"])
        while not stop_event.is_set():
            ts = dt.datetime.now(dt.timezone.utc).isoformat()
            out = compose_exec(container, "ss -ti dst receiver", check=False).stdout
            cwnd, rtt, pacing = parse_metrics(out)
            writer.writerow([ts, container, cwnd, rtt, pacing, " ".join(out.split())])
            f.flush()
            time.sleep(1)


def run_iperf(container, algorithm, seconds):
    return compose_exec(container, f"iperf3 -c receiver -t {seconds} -i 1 -J -C {algorithm}")


def save_json(path, content):
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = {"raw": content}
    path.write_text(json.dumps(data, indent=2))


def run_single_test(base_dir, profile, algorithm, duration, container="sender"):
    print(f"Running {profile} + {algorithm} on {container}")
    test_dir = base_dir / f"{profile}_{algorithm}_{container}"
    test_dir.mkdir(parents=True, exist_ok=True)

    apply_profile(container, profile)
    set_algorithm(container, algorithm)

    stop_event = threading.Event()
    t = threading.Thread(
        target=telemetry_loop,
        args=(container, test_dir / "telemetry.csv", stop_event),
        daemon=True,
    )
    t.start()

    try:
        result = run_iperf(container, algorithm, duration)
        save_json(test_dir / "iperf3.json", result.stdout)
    finally:
        stop_event.set()
        t.join(timeout=3)
        clear_tc(container)


def run_fairness_test(base_dir, duration, profile="wired", a1="bbr", a2="cubic"):
    print(f"Running fairness test: sender({a1}) vs sender2({a2}) with {profile}")
    test_dir = base_dir / f"fairness_{a1}_vs_{a2}_{profile}"
    test_dir.mkdir(parents=True, exist_ok=True)

    for c in ("sender", "sender2"):
        apply_profile(c, profile)

    set_algorithm("sender", a1)
    set_algorithm("sender2", a2)

    stop1 = threading.Event()
    stop2 = threading.Event()
    t1 = threading.Thread(target=telemetry_loop, args=("sender", test_dir / "sender_telemetry.csv", stop1), daemon=True)
    t2 = threading.Thread(target=telemetry_loop, args=("sender2", test_dir / "sender2_telemetry.csv", stop2), daemon=True)
    t1.start()
    t2.start()

    p1 = subprocess.Popen(
        ["docker", "compose", "exec", "-T", "sender", "bash", "-lc", f"iperf3 -c receiver -t {duration} -i 1 -J -C {a1}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    p2 = subprocess.Popen(
        ["docker", "compose", "exec", "-T", "sender2", "bash", "-lc", f"iperf3 -c receiver -t {duration} -i 1 -J -C {a2}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    try:
        out1, _ = p1.communicate()
        out2, _ = p2.communicate()
        save_json(test_dir / "sender_iperf3.json", out1 or "")
        save_json(test_dir / "sender2_iperf3.json", out2 or "")
    finally:
        stop1.set()
        stop2.set()
        t1.join(timeout=3)
        t2.join(timeout=3)
        clear_tc("sender")
        clear_tc("sender2")


def ensure_lab_up(setup_bbr):
    run(["docker", "compose", "up", "-d", "--build"], capture=False)
    if setup_bbr:
        run(["bash", "scripts/setup_bbr.sh", "sender", "sender2"], capture=False)


def main():
    parser = argparse.ArgumentParser(description="Run Reno/CUBIC/BBR comparison experiments in Docker lab")
    parser.add_argument("--duration", type=int, default=60, help="iperf3 duration per test (seconds)")
    parser.add_argument("--output-root", default="results", help="directory root for experiment artifacts")
    parser.add_argument("--skip-setup-bbr", action="store_true", help="skip scripts/setup_bbr.sh")
    parser.add_argument("--down", action="store_true", help="docker compose down after tests")
    args = parser.parse_args()

    run_id = dt.datetime.now(dt.timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
    base_dir = Path(args.output_root) / run_id
    base_dir.mkdir(parents=True, exist_ok=True)

    ensure_lab_up(setup_bbr=not args.skip_setup_bbr)

    try:
        for algorithm in ALGORITHMS:
            run_single_test(base_dir, "wired", algorithm, args.duration)
            run_single_test(base_dir, "wireless", algorithm, args.duration)
        run_fairness_test(base_dir, args.duration, profile="wired", a1="bbr", a2="cubic")
    finally:
        clear_tc("sender")
        clear_tc("sender2")
        if args.down:
            run(["docker", "compose", "down"], capture=False)

    print(f"Completed. Results written to: {base_dir}")


if __name__ == "__main__":
    main()
