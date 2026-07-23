# Installing External Popper Without Admin Privileges

This repo now uses Popper as an external command instead of vendoring Popper's Python sources.

MARITA expects a command named `run-popper` on `PATH`, or a custom command via:

```bash
export MARITA_POPPER_CMD="$HOME/bin/run-popper"
```

You can also set it in a benchmark config:

```yaml
benchmark:
  baseline: POPPER
  timeout: 3600
  memory_gb: 10
  popper_command: run-popper
```

## Context

- Server has no `sudo` access.
- Python 3.12 is installed system-wide.
- `uv` is not installed initially.
- Popper current `main` requires Python 3.14.
- Popper depends on `janus-swi`.
- `janus-swi` needs SWI-Prolog visible during build.
- Everything is installed in user space.

## 1. Install `uv` Locally

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Add `uv` to `PATH` for the current shell:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Make it persistent:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

Check:

```bash
uv --version
which uv
```

## 2. Install Python 3.14 Locally With `uv`

Popper current `main` requires Python `>= 3.14`, so Python 3.12 is insufficient.

```bash
uv python install 3.14
```

Check:

```bash
uv python list
```

## 3. Clone Popper

```bash
cd ~
git clone https://github.com/logic-and-learning-lab/Popper.git
cd ~/Popper
```

## 4. First Popper Attempt

```bash
uv run --python 3.14 popper.py examples/iggp-rps-next-score
```

Expected possible failure:

```text
RuntimeError: Failed to find SWI-Prolog components
```

Reason: Popper depends on `janus-swi`, and `janus-swi` must find a SWI-Prolog installation during build.

## 5. Install `micromamba` Locally

```bash
mkdir -p ~/micromamba
cd ~/micromamba
curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xvj bin/micromamba
```

Initialize it for `bash`:

```bash
./bin/micromamba shell init -s bash -r "$HOME/micromamba"
source ~/.bashrc
```

Check:

```bash
micromamba --version
```

## 6. Install SWI-Prolog Locally With Micromamba

Create a dedicated environment:

```bash
micromamba create -n popper-tools -c conda-forge swi-prolog -y
```

Activate it:

```bash
micromamba activate popper-tools
```

Check that `swipl` is available:

```bash
which swipl
swipl --version
```

## 7. Expose SWI-Prolog To `janus-swi`

Still inside the activated `popper-tools` environment:

```bash
export SWIPL="$(which swipl)"
export PATH="$(dirname "$SWIPL"):$PATH"
```

Check:

```bash
echo "$SWIPL"
which swipl
swipl --version
```

## 8. Retry Popper

```bash
cd ~/Popper
micromamba activate popper-tools
export SWIPL="$(which swipl)"
export PATH="$(dirname "$SWIPL"):$PATH"
uv run --python 3.14 popper.py examples/iggp-rps-next-score
```

## Convenience Wrapper Required By MARITA

Create `~/bin/run-popper`:

```bash
mkdir -p ~/bin
cat > ~/bin/run-popper <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

source "$HOME/.bashrc"
micromamba activate popper-tools

export SWIPL="$(which swipl)"
export PATH="$(dirname "$SWIPL"):$PATH"

cd "$HOME/Popper"
uv run --python 3.14 popper.py "$@"
EOF

chmod +x ~/bin/run-popper
```

Ensure `~/bin` is on `PATH`:

```bash
export PATH="$HOME/bin:$PATH"
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc
```

Test:

```bash
run-popper examples/iggp-rps-next-score
```

## MARITA Smoke Test

```bash
uv run marita paper-benchmark \
  --database-dir data/relational \
  --output results/smoke_popper \
  --logs logs/smoke_popper \
  --algorithms POPPER \
  --databases Biodegradability \
  --timeout 300 \
  --memory-gb 2
```
