FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100 \
    DOCKER=true \
    HEROKU_NO_GIT=1 \
    RATKO_NO_GIT=1 \
    GIT_PYTHON_REFRESH=quiet

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        build-essential \
        ffmpeg \
        gcc \
        git \
        libcairo2 \
        libmagic1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade -r requirements.txt \
    && python -c 'import hashlib, pathlib; p = pathlib.Path("requirements.txt"); pathlib.Path(".requirements_hash").write_text(hashlib.sha256(p.read_bytes()).hexdigest())'

COPY . .
RUN mkdir -p /data/sessions

VOLUME ["/data"]
CMD ["python", "-m", "heroku", "--root", "--data-root", "/data", "--no-git"]
