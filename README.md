# bbr

Docker-based lab for TCP Reno, CUBIC, and BBR comparison.

## Files
- `Dockerfile`
- `docker-compose.yml`
- `scripts/setup_bbr.sh`
- `experiment_runner.py`
- `results/` (timestamped JSON/CSV outputs)

## Quick start
```bash
docker compose up -d --build
bash scripts/setup_bbr.sh sender sender2
python3 experiment_runner.py --duration 60
```

## What is collected
- `iperf3` JSON outputs for each profile/algorithm combination.
- `ss -ti` telemetry sampled every second into CSV (cwnd, rtt, pacing rate).
- Fairness run with two concurrent senders (`bbr` vs `cubic`) under wired settings.

The runner clears `tc` rules between tests to avoid impairment bleeding.
