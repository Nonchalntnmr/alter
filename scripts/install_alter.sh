#!/usr/bin/env bash
# Install, upgrade, roll back or uninstall ALTER (the contextual-intelligence package) for one person.
#
#   scripts/install_alter.sh                        build a wheel from this checkout and install it
#   scripts/install_alter.sh path/to/contextual_intelligence-<version>-py3-none-any.whl
#   scripts/install_alter.sh --rollback path/to/an-older.whl
#   scripts/install_alter.sh --uninstall
#
# Options:
#   --no-semantic        leave out the local embedding model (sentence-transformers and torch, about 1 GB)
#   --no-web             leave out the web dashboard, `pcim serve` (fastapi and uvicorn, a few MB)
#   --python PY          the interpreter for a new venv (default: the newest python3.1x >= 3.12 on PATH)
#   --constraints FILE   pins for every dependency (default: requirements-lock.txt next to this script)
#   --no-doctor          do not run `alter doctor` at the end
#   --force              go ahead even while a goal worker from this install is running
#
# Where things go:
#   ALTER_HOME (default ~/.alter)       venv/, and wheels/: a copy of every wheel installed, for rollback
#   ALTER_BIN  (default ~/.local/bin)   symlinks: alter, ci, pcim
# Your data lives in PCIM_HOME (default ~/.pcim). This script never changes it.
#
# Run it again to repair an install, or with a newer wheel to upgrade. A file in ALTER_BIN that is not
# this install's symlink is moved to NAME.pre-alter first; --uninstall puts it back.
# macOS ships bash 3.2: no associative arrays, no ${x,,}, no empty "${a[@]}" under set -u.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ALTER_HOME="${ALTER_HOME:-$HOME/.alter}"
ALTER_BIN="${ALTER_BIN:-$HOME/.local/bin}"
COMMANDS="alter ci pcim"
export PIP_DISABLE_PIP_VERSION_CHECK=1
unset PYTHONPATH  # a checkout on it would shadow the installed package, for pip and for `alter doctor`

say() { printf 'install_alter: %s\n' "$*"; }
die() { printf 'install_alter: %s\n' "$*" >&2; exit 1; }
usage() { sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

mode=install wheel="" semantic=1 web=1 doctor=1 force=0 python="" constraints=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --uninstall) mode=uninstall ;;
        --rollback) [[ $# -ge 2 ]] || die "--rollback needs the wheel to go back to"
                    mode=rollback; wheel="$2"; shift ;;
        --no-semantic) semantic=0 ;;
        --no-web) web=0 ;;
        --no-doctor) doctor=0 ;;
        --force) force=1 ;;
        --python) [[ $# -ge 2 ]] || die "--python needs an interpreter"; python="$2"; shift ;;
        --constraints) [[ $# -ge 2 ]] || die "--constraints needs a file"; constraints="$2"; shift ;;
        -h|--help) usage; exit 0 ;;
        -*) die "unknown option $1 (--help lists them)" ;;
        *) [[ -z "$wheel" ]] || die "one wheel at a time"; wheel="$1" ;;
    esac
    shift
done

# Absolute paths, so the symlinks and the checks on them do not depend on the current directory.
abspath() { (cd "$1" && pwd -P); }
if [[ -n "$wheel" && $mode != uninstall ]]; then
    [[ -f "$wheel" ]] || die "no wheel at $wheel"
    case "$(basename "$wheel")" in
        contextual_intelligence-*-py3-none-any.whl) ;;
        *) die "$(basename "$wheel") is not a contextual-intelligence wheel" ;;
    esac
    wheel="$(abspath "$(dirname "$wheel")")/$(basename "$wheel")"
fi
if [[ $mode != uninstall ]]; then
    mkdir -p "$ALTER_HOME" "$ALTER_BIN"
fi
[[ -d "$ALTER_HOME" ]] && ALTER_HOME="$(abspath "$ALTER_HOME")"
bin_as_given="$ALTER_BIN"
[[ -d "$ALTER_BIN" ]] && ALTER_BIN="$(abspath "$ALTER_BIN")"
venv="$ALTER_HOME/venv"
wheels="$ALTER_HOME/wheels"

py_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; }
py_ver() { "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])'; }

pick_python() {
    if [[ -n "$python" ]]; then
        py_ok "$python" || die "$python is not a Python >= 3.12"
        command -v "$python"
        return
    fi
    local c
    for c in python3.14 python3.13 python3.12 python3; do
        if command -v "$c" >/dev/null 2>&1 && py_ok "$c"; then
            command -v "$c"
            return
        fi
    done
    die "no Python >= 3.12 on PATH (macOS's own python3 is older). Install one, for example
    brew install python@3.13      (or the installer from python.org)
then run this again, or pass --python /path/to/python3.x"
}

ensure_venv() {
    local py
    if [[ -x "$venv/bin/python" ]] && py_ok "$venv/bin/python"; then
        if [[ -z "$python" || "$(py_ver "$venv/bin/python")" == "$(py_ver "$python")" ]]; then
            say "venv: $venv (kept; Python $(py_ver "$venv/bin/python"))"
            return
        fi
        say "venv: $venv has Python $(py_ver "$venv/bin/python"); --python asks for $(py_ver "$python"): recreating it"
    fi
    py="$(pick_python)"
    if [[ -e "$venv" ]]; then
        [[ -f "$venv/pyvenv.cfg" ]] || die "$venv exists and is not a venv; move it away first"
        if [[ ! -x "$venv/bin/python" ]] || ! "$venv/bin/python" -c '' >/dev/null 2>&1; then
            say "venv: $venv does not run (its Python was removed?): recreating it"
        elif ! py_ok "$venv/bin/python"; then
            say "venv: $venv has Python $(py_ver "$venv/bin/python"), older than 3.12: recreating it"
        fi
        "$py" -m venv --clear "$venv"
    else
        "$py" -m venv "$venv"
    fi
    say "venv: $venv (Python $(py_ver "$venv/bin/python"), from $py)"
}

installed_version() {
    [[ -x "$venv/bin/python" ]] || return 0
    "$venv/bin/python" - <<'EOF' 2>/dev/null || true
import importlib.metadata as m
try:
    print(m.version("contextual-intelligence"))
except m.PackageNotFoundError:
    pass
EOF
}

# What installing NEW over OLD is: upgrade, downgrade or reinstall (pip's own version rules).
compare() {
    "$venv/bin/python" - "$1" "$2" <<'EOF' 2>/dev/null || echo replace
import sys
from pip._vendor.packaging.version import Version
old, new = map(Version, sys.argv[1:])
print("upgrade" if new > old else "downgrade" if new < old else "reinstall")
EOF
}

# Goal workers run `python -P -m pcim.goals run …` from this venv; replacing the package under one breaks it.
running_workers() { pgrep -f "$venv/bin/python[0-9.]* -P -m pcim\.goals run" 2>/dev/null || true; }

refuse_if_running() {
    local pids
    pids="$(running_workers)"
    [[ -z "$pids" || $force == 1 ]] && return 0
    die "a goal is running from this install (pid $(echo $pids)). Pause it (alter goals pause <goal>) or let it
finish, then run this again; --force goes ahead anyway"
}

build_wheel() {  # $1: an empty directory to build in
    [[ -f "$repo/pyproject.toml" && -d "$repo/src" ]] || die "no wheel given, and $repo is not a checkout to build one from"
    local work="$1"
    # a copy, so the checkout gets no build/ or egg-info, and no stale build/lib reaches the wheel
    cp "$repo/pyproject.toml" "$repo/README.md" "$work/"
    cp -R "$repo/src" "$work/src"
    rm -rf "$work"/src/*.egg-info
    find "$work/src" -name __pycache__ -type d -prune -exec rm -rf {} +
    say "building a wheel from $repo" >&2
    "$venv/bin/python" -m pip wheel --quiet --no-deps -w "$work/dist" "$work" >&2
    ls "$work"/dist/contextual_intelligence-*.whl
}

cache_wheel() (
    local whl="$1" name="$2" cached staged digest
    mkdir -p "$wheels"
    cached="$wheels/$name"
    if cmp -s "$whl" "$cached"; then
        printf '%s\n' "$cached"
        return
    fi

    staged="$(mktemp "$wheels/.wheel.XXXXXX")"
    trap 'rm -f "$staged"' EXIT
    cp "$whl" "$staged"
    # Publish a complete copy without replacing one already cached under this filename.
    if [[ ! -e "$cached" ]]; then
        ln "$staged" "$cached" 2>/dev/null || true
    fi
    if ! cmp -s "$staged" "$cached"; then
        digest="$("$venv/bin/python" - "$staged" <<'EOF'
import hashlib
import sys

with open(sys.argv[1], "rb") as wheel:
    print(hashlib.file_digest(wheel, "sha256").hexdigest())
EOF
)"
        cached="$wheels/rebuilds/$digest/$name"
        mkdir -p "$(dirname "$cached")"
        if [[ ! -e "$cached" ]]; then
            ln "$staged" "$cached" 2>/dev/null || true
        fi
        cmp -s "$staged" "$cached" || die "could not cache the wheel at $cached"
    fi
    printf '%s\n' "$cached"
)

install_wheel() {
    local whl="$1" name ver old extras="" kind cached
    name="$(basename "$whl")"
    ver="${name#contextual_intelligence-}"
    ver="${ver%%-*}"
    old="$(installed_version)"
    if [[ -z "$old" ]]; then
        say "installing $ver"
    else
        kind="$(compare "$old" "$ver")"
        say "$kind: $old -> $ver"
        if [[ $mode == rollback && $kind != downgrade ]]; then
            say "note: $ver is not older than the installed $old; installing it anyway"
        fi
    fi
    cached="$(cache_wheel "$whl" "$name")"
    if [[ "$cached" != "$wheels/$name" ]]; then
        say "saved this rebuild at $cached"
    fi
    [[ $semantic == 1 ]] && extras="semantic"
    [[ $web == 1 ]] && extras="${extras:+$extras,}api"
    extras="${extras:+[$extras]}"
    "$venv/bin/python" -m pip install --quiet -c "$constraints" "$cached$extras"
    # The line above skips a wheel whose version is already installed (a rebuild of the same version):
    # this puts this wheel's files in place either way, without touching the dependencies.
    "$venv/bin/python" -m pip install --quiet --no-deps --force-reinstall "$cached"
    local left_out=""
    [[ $semantic == 1 ]] || left_out="the semantic extra"
    [[ $web == 1 ]] || left_out="${left_out:+$left_out and }the web dashboard"
    say "installed $(installed_version) in $venv${left_out:+ (without $left_out)}"
}

link_commands() {
    local c src dst backup
    for c in $COMMANDS; do
        src="$venv/bin/$c"
        dst="$ALTER_BIN/$c"
        if [[ ! -e "$src" ]]; then  # this version has no such command (0.2 had no `alter`)
            if [[ -L "$dst" && "$(readlink "$dst")" == "$src" ]]; then
                rm -f "$dst"
                say "removed $dst: this version has no $c"
            fi
            continue
        fi
        if [[ -L "$dst" && "$(readlink "$dst")" == "$src" ]]; then
            continue
        fi
        if [[ -d "$dst" && ! -L "$dst" ]]; then
            die "$dst is a directory; move it away first"
        fi
        if [[ -e "$dst" || -L "$dst" ]]; then
            backup="$dst.pre-alter"
            [[ -e "$backup" || -L "$backup" ]] && backup="$dst.pre-alter.$(date +%Y%m%d%H%M%S)"
            mv "$dst" "$backup"
            say "kept your $dst as $backup"
        fi
        ln -s "$src" "$dst"
        say "linked $dst"
    done
    case ":$PATH:" in
        *":$bin_as_given:"* | *":$ALTER_BIN:"*) ;;
        *) say "$ALTER_BIN is not on your PATH. Add it, then open a new terminal:"
           printf "    echo 'export PATH=\"%s:\$PATH\"' >> ~/.zshrc\n" "$ALTER_BIN" ;;
    esac
}

run_doctor() {
    [[ $doctor == 1 ]] || return 0
    local cmd="$ALTER_BIN/alter"
    [[ -x "$cmd" ]] || cmd="$ALTER_BIN/ci"  # a version without `alter`
    say "$(basename "$cmd") doctor:"
    if "$cmd" doctor; then
        say "doctor: nothing blocking"
    elif [[ ! -f "${PCIM_HOME:-$HOME/.pcim}/token" ]]; then
        say "doctor: no identity yet, as expected on a new computer. Next: alter setup"
    else
        say "doctor: a blocking problem (above). The install itself is complete."
    fi
}

uninstall() {
    local c dst
    refuse_if_running
    for c in $COMMANDS; do
        dst="$ALTER_BIN/$c"
        if [[ -L "$dst" && "$(readlink "$dst")" == "$venv/bin/$c" ]]; then
            rm -f "$dst"
            say "removed $dst"
        fi
        if [[ ( -e "$dst.pre-alter" || -L "$dst.pre-alter" ) && ! -e "$dst" && ! -L "$dst" ]]; then
            mv "$dst.pre-alter" "$dst"
            say "put back $dst (from $dst.pre-alter)"
        fi
    done
    if [[ -d "$venv" ]]; then
        [[ -f "$venv/pyvenv.cfg" ]] || die "$venv is not a venv; not removing it"
        rm -rf "$venv"
        say "removed $venv"
    fi
    say "kept: $wheels (the wheels, for a reinstall) and your data in ${PCIM_HOME:-$HOME/.pcim}. Delete them by hand to remove them too."
}

if [[ $mode == uninstall ]]; then
    uninstall
    exit 0
fi

constraints="${constraints:-$repo/requirements-lock.txt}"
[[ -f "$constraints" ]] || die "no constraints file at $constraints (pass --constraints FILE)"
refuse_if_running
ensure_venv
if [[ -z "$wheel" ]]; then
    build_dir="$(mktemp -d "${TMPDIR:-/tmp}/alter-build.XXXXXX")"
    trap 'rm -rf "$build_dir"' EXIT
    wheel="$(build_wheel "$build_dir")"
fi
install_wheel "$wheel"
link_commands
run_doctor
