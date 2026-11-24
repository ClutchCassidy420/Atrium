# The REA workbench: REA (pinned) and Ghidra (pinned, checksum-checked) in a container with no way out to the internet.
# Network is needed only to BUILD this image. Updating REA or Ghidra means changing the versions below and rebuilding.
FROM eclipse-temurin:25-jdk AS jdk

FROM node:24-bookworm-slim
ARG REA_VERSION=6.10.1
ARG GHIDRA_URL=https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.2_build/ghidra_12.2_PUBLIC_20261016.zip
ARG GHIDRA_SHA256=2c2cd776db3c1f6a097ba418922378ac3649cadd5e844a6f5f088ae628a7be67
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip procps unzip curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=jdk /opt/java/openjdk /opt/java/openjdk
ENV JAVA_HOME=/opt/java/openjdk PATH=/opt/java/openjdk/bin:$PATH
RUN curl -fsSL -o /tmp/ghidra.zip "$GHIDRA_URL" \
 && echo "$GHIDRA_SHA256  /tmp/ghidra.zip" | sha256sum -c - \
 && unzip -q /tmp/ghidra.zip -d /opt && mv /opt/ghidra_* /opt/ghidra && rm /tmp/ghidra.zip
ENV GHIDRA_INSTALL_DIR=/opt/ghidra
RUN npm install --global "rea-agents@${REA_VERSION}" && npm cache clean --force
RUN pip install --no-cache-dir --break-system-packages fastapi==0.115.6 "uvicorn==0.34.0"
# an ordinary user with no sudo; the work folder is the only place it can write
ARG UID=2100
ARG GID=2100
RUN groupadd -g ${GID} rea && useradd -m -u ${UID} -g ${GID} -s /usr/sbin/nologin rea && mkdir -p /work && chown rea:rea /work
WORKDIR /srv/rea
COPY server.py /srv/rea/server.py
USER rea
ENV HOME=/work/home REA_WORK=/work
EXPOSE 9100
CMD ["python3", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9100", "--no-server-header"]
