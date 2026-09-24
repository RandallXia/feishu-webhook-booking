FROM python:3.12-alpine

# Patch CVEs in the base image's system packages (openssl, util-linux, etc.)
# by upgrading all apk packages to the latest available versions.
RUN apk upgrade --no-cache

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r /app/requirements.txt && \
    pip install --no-cache-dir --upgrade setuptools

COPY app /app/app

EXPOSE 2398

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "2398"]
