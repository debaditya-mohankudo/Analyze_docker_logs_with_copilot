# SSH-reachable Docker-in-Docker target for testing remote/SSH log collection
# without a real remote Linux box. Runs its own dockerd (DinD) plus sshd, so
# `docker -H ssh://root@localhost:2222 ...` resolves to a genuinely separate,
# live Docker daemon reachable only over SSH — exercising the same code path
# as a real pre-authenticated remote host.
FROM docker:27-dind

RUN apk update && \
    apk upgrade --no-cache && \
    apk add --no-cache openssh-server && \
    ssh-keygen -A && \
    mkdir -p /root/.ssh && \
    chmod 700 /root/.ssh && \
    # Key-only auth: no passwords, no interactive prompts (BatchMode-compatible).
    sed -i \
        -e 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' \
        -e 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' \
        -e 's/^#\?ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/' \
        -e 's/^#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' \
        /etc/ssh/sshd_config

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22
ENTRYPOINT ["/entrypoint.sh"]
