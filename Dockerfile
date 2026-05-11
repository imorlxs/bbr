FROM debian:bookworm-slim

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        bash \
        iperf3 \
        iproute2 \
        iputils-ping \
        procps \
    && rm -rf /var/lib/apt/lists/*

CMD ["bash", "-lc", "sleep infinity"]
