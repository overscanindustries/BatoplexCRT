FROM debian:trixie-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pygame mpv xvfb xdotool x11-utils x11-xserver-utils x11-apps netpbm curl ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
