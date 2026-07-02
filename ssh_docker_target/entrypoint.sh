#!/bin/sh
# Start the inner Docker daemon (DinD) in the background, then run sshd in
# the foreground. `authorized_keys` is bind-mounted in by docker-compose.test.yml
# from a keypair generated per test session (see tests/conftest.py) — never
# baked into the image or committed to the repo.
set -e

dockerd-entrypoint.sh &

# Wait for the inner daemon's socket before declaring ready; sshd itself
# doesn't need it, but anything ssh'ing in immediately after connect will.
for i in $(seq 1 30); do
    [ -S /var/run/docker.sock ] && break
    sleep 1
done

exec /usr/sbin/sshd -D -e
