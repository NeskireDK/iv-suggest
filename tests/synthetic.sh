#!/bin/sh
set -eu
here=$(cd "$(dirname "$0")" && pwd)

if ! docker info >/dev/null 2>&1; then
    echo "SKIP: docker is not available, and the synthetic harness needs a"
    echo "      throwaway postgres. The rest of the suite runs without it:"
    echo "      python3 -m unittest discover -s tests -t tests"
    exit 0
fi

sweep() {
    docker ps -aq --filter 'name=^ivs-synth-' | xargs -r docker rm -f -v >/dev/null
}

sweep
trap sweep EXIT INT TERM
python3 -m unittest discover -s "$here" -t "$here" -p 'test_synthetic*.py' "$@"
