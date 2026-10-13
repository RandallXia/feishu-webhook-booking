FROM python:3.12-alpine

# Patch CVEs in the base image's system packages (openssl, util-linux, etc.)
# by upgrading all apk packages to the latest available versions.
RUN apk upgrade --no-cache

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
# Upgrade build/packaging tooling FIRST (pip + setuptools), then the app deps.
# The base image ships an old setuptools; Scout-class scanners flag it (and any
# stale transitive CVE carriers) unless forced to latest in the same layer as
# the dependency install. Tsinghua index applies to the app deps; pip itself
# upgrades fine from the default index inside alpine (keep one combined layer
# so a rebuild always re-resolves all of them to current).
RUN pip install --no-cache-dir --upgrade pip setuptools && \
    pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r /app/requirements.txt && \
    pip install --no-cache-dir --upgrade setuptools

COPY app /app/app

EXPOSE 2398

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "2398"]
