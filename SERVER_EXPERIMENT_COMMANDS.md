# Four-Server Launch-Once Pipeline

Use this runbook on the shared filesystem used by:

- `tipi00`
- `tipi01`
- `tipi02`
- `tipi04`

The pipeline uses `configs/paper/benchmark_83.yaml` and a shared dynamic queue under `results/paper_pipeline/`. Databases are not statically assigned to servers. Each server claims the next pending job, runs it, and then claims another job.

## 1. Setup On Each Server

Run on each server:

```bash
cd /home/paris/dev/py/mahilda
uv sync
mkdir -p logs/paper_pipeline results/paper_pipeline
```

Check the hostname:

```bash
hostname -s
```

It must be one of:

```text
tipi00
tipi01
tipi02
tipi04
```

## 2. Wait For Databases


The final pipeline requires exactly 83 converted `.db` files because `configs/paper/benchmark_83.yaml` contains `expected_databases: 83`.

Check once from any server:

```bash
cd /home/paris/dev/py/mahilda
python3 - <<'PY'
from pathlib import Path
dbs = sorted(Path("data/relational").glob("*.db"), key=lambda p: p.name.lower())
print(len(dbs))
for db in dbs:
    print(db.name)
PY
```

Do not launch the real pipeline until the count is `83`.

## 3. Dry Run

Before the full pipeline, verify external POPPER works on each server:

```bash
run-popper "$HOME/Popper/examples/iggp-rps-next-score"
```

If `run-popper` is missing, follow `docs/popper-user-space-install.md`.

Run on each server:

```bash
cd /home/paris/dev/py/mahilda
uv run mahilda paper-pipeline \
  --settings configs/paper/benchmark_83.yaml \
  --host auto \
  --dry-run
```

Expected dry-run total with 83 DBs:

```text
Planned jobs: 519
010_setup: 1 jobs
020_b2_disjointness: 20 jobs
030_b1_mahilda: 83 jobs
040_b1_spider: 83 jobs
050_b3_joinability: 166 jobs
060_b1_amie3: 83 jobs
070_b1_popper: 83 jobs
```

Dry-run does not create or consume the queue.

## 4. Launch Once

Run this one command on each server:

```bash
cd /home/paris/dev/py/mahilda
mkdir -p logs/paper_pipeline
nohup uv run mahilda paper-pipeline \
  --settings configs/paper/benchmark_83.yaml \
  --host auto \
  > "logs/paper_pipeline/runner_$(hostname -s).log" 2>&1 &
```

After the log file appears and shows the server is either initializing, claiming, or waiting for jobs, you can disconnect.

Check the local server log:

```bash
tail -f "logs/paper_pipeline/runner_$(hostname -s).log"
```

Useful commands after launch:

| Command | Utility |
| --- | --- |
| `Ctrl-C` | Stop `tail -f` only. This does not stop the `nohup` runner. |
| `jobs -l` | Show background jobs in the current shell, including the runner PID. |
| `ps -fp <PID>` | Check what a printed PID is running. For example, use this for the PID printed by `nohup`. |
| `disown` | Detach the background runner from the current shell before exiting SSH. |
| `exit` | Close the SSH session after the runner has been started with `nohup ... &`. |

If the same stage appears in multiple logs, that is expected. All servers work on the same shared stage, but each server claims different jobs from the queue.

## 5. Pipeline Order

The pipeline runs stages in this order:

```text
010_setup             host/table setup work
020_b2_disjointness   B-2 on Biodegradability only
030_b1_mahilda        B-1 MAHILDA on all 83 DBs
040_b1_spider         B-1 SPIDER on all 83 DBs
050_b3_joinability    B-3 FK/full joinability on all 83 DBs
060_b1_amie3          B-1 AMIE3 on all 83 DBs
070_b1_popper         B-1 POPPER on all 83 DBs
```

Within a stage, jobs are dynamically claimed from the shared queue, so a slow database does not remain permanently tied to one server.

## 6. Monitor Status

Run from any server:

```bash
cd /home/paris/dev/py/mahilda
uv run mahilda paper-pipeline \
  --settings configs/paper/benchmark_83.yaml \
  --status
```

Also useful:

```bash
ls -lh logs/paper_pipeline/runner_*.log
find results/paper_pipeline/queue -maxdepth 3 -type f | wc -l
```

## 7. Resume

If a server rebooted or the runner stopped, rerun the same launch command on that server:

```bash
cd /home/paris/dev/py/mahilda
nohup uv run mahilda paper-pipeline \
  --settings configs/paper/benchmark_83.yaml \
  --host auto \
  > "logs/paper_pipeline/runner_$(hostname -s).log" 2>&1 &
```

The runner resumes from the shared queue. Completed jobs are not rerun.

## 8. Restart After Config Changes

Restart the runner after changing worker counts or resource limits in `configs/paper/benchmark_83.yaml`. Settings are loaded only when the runner starts, so already-running processes do not see YAML edits.

Do not run `--reset` for a restart. Resetting discards the shared queue state.

Run on each server:

```bash
cd /home/paris/dev/py/mahilda
pgrep -af '[m]ahilda paper-pipeline.*configs/paper/benchmark_83.yaml'
```

It is normal to see two matching processes for one runner:

```text
75637 uv run mahilda paper-pipeline --settings configs/paper/benchmark_83.yaml --host auto
75645 /people/paris/mahilda/.venv/bin/python3 /people/paris/mahilda/.venv/bin/mahilda paper-pipeline --settings configs/paper/benchmark_83.yaml --host auto
```

The first process is the `uv run` wrapper. The second process is the actual Python `mahilda paper-pipeline` child. Stop both PIDs for a clean restart:

```bash
kill -TERM 75637 75645
sleep 10
pgrep -af '[m]ahilda paper-pipeline.*configs/paper/benchmark_83.yaml'
```

If either PID is still listed:

```bash
kill -KILL 75637 75645
```

If AMIE3 or POPPER was active when the runner was stopped, check for orphaned child jobs before relaunching:

```bash
pgrep -af '[m]ahilda.cli.main benchmark.*--baseline (AMIE3|POPPER)|[j]ava.*amie-milestone-intKB.jar|[r]un-popper'
```

If those processes are still running and you intentionally want a hard restart, stop them too:

```bash
pkill -TERM -f '[m]ahilda.cli.main benchmark.*--baseline (AMIE3|POPPER)'
pkill -TERM -f '[j]ava.*amie-milestone-intKB.jar'
pkill -TERM -f '[r]un-popper'
sleep 10
pkill -KILL -f '[m]ahilda.cli.main benchmark.*--baseline (AMIE3|POPPER)'
pkill -KILL -f '[j]ava.*amie-milestone-intKB.jar'
pkill -KILL -f '[r]un-popper'
```

Relaunch exactly one runner per server:

```bash
cd /home/paris/dev/py/mahilda
mkdir -p logs/paper_pipeline
nohup uv run mahilda paper-pipeline \
  --settings configs/paper/benchmark_83.yaml \
  --host auto \
  > "logs/paper_pipeline/runner_$(hostname -s).log" 2>&1 &
```

Verify the restart:

```bash
pgrep -af '[m]ahilda paper-pipeline.*configs/paper/benchmark_83.yaml'
tail -f "logs/paper_pipeline/runner_$(hostname -s).log"
```

If active jobs were killed, their queue markers can remain in `running` until stale recovery requeues them. With the current config, `stale_after_seconds: 4200` means this can take up to about 70 minutes.

## 9. Reset

Only use this before a real launch, or if you intentionally want to discard the shared queue state:

```bash
cd /home/paris/dev/py/mahilda
uv run mahilda paper-pipeline \
  --settings configs/paper/benchmark_83.yaml \
  --reset
```

Do not reset while jobs are running.

## 10. Final Artifacts

When the pipeline finishes, `tipi00` performs aggregation and writes:

```text
results/paper_table1/relational_counts.tsv
results/paper_table2/table2.tsv
results/ablation_disjoint/figure_coords.tsv
results/ablation_join/comparison.tsv
results/paper_alignment_<YYYY-MM-DD>.md
results/paper_pipeline/pipeline.complete
```

## Emergency Stop

To stop the runner on the current server:

```bash
pkill -TERM -f 'mahilda paper-pipeline'
```

To stop one specific PID after verifying it with `ps -fp <PID>`:

```bash
kill <PID>
```

If a process does not exit after a short wait:

```bash
pkill -KILL -f 'mahilda paper-pipeline'
```

Or, for one specific PID only:

```bash
kill -9 <PID>
```

Use `SIGKILL` only as a last resort.
