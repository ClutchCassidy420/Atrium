FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git tmux curl ripgrep procps nodejs npm openssh-client ca-certificates sudo chromium fonts-liberation fonts-noto-color-emoji \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir fastapi==0.115.6 "uvicorn==0.34.0" websockets==13.1
# The service and the terminal run as an ordinary user whose ids match the agent's, so files made by either of you can be changed by the other.
# That user can use sudo inside this container (install packages and so on); the container itself only sees the workspace folder.
ARG UID=2000
ARG GID=2000
RUN groupadd -g ${GID} code && useradd -m -u ${UID} -g ${GID} -s /bin/bash code \
 && echo "code ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/code && chmod 440 /etc/sudoers.d/code
ENV HOME=/home/code
WORKDIR /srv/code
COPY server.py /srv/code/server.py
USER code
EXPOSE 9000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9000", "--no-server-header"]
