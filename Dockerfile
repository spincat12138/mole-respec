FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DISPLAY=:99 \
    QT_QPA_PLATFORM=xcb \
    QT_X11_NO_MITSHM=1 \
    HOME=/home/app \
    XDG_CONFIG_HOME=/data/config \
    XDG_RUNTIME_DIR=/tmp/mole-respec-runtime \
    TZ=Asia/Shanghai

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates fonts-noto-cjk \
        libdbus-1-3 libegl1 libfontconfig1 libgl1 libglib2.0-0 libopengl0 \
        libx11-xcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
        libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 \
        libxcb-xfixes0 libxcb-xkb1 libxkbcommon-x11-0 \
        novnc openbox tini tzdata websockify x11-utils x11vnc xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app \
    && install -d -o app -g app /data /data/config

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY ref/moer.py ref/item_names.py ref/pet_names.py ref/pet_bases.json ref/skills.xml ref/logo.webp ref/thanks_header.png ./ref/
COPY docker/ /opt/mole-respec/
# Preserve the desktop database path while storing the actual file in the volume.
RUN ln -s /data/pet_results.db /app/pet_results.db

USER app
EXPOSE 6080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "/opt/mole-respec/start.py", "--healthcheck"]
ENTRYPOINT ["/usr/bin/tini", "--", "python", "/opt/mole-respec/start.py"]
