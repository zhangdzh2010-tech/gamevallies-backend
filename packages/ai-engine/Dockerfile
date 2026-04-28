FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install --with-deps chromium

COPY src/ ./src/
# contracts/ is mirrored here by scripts/deploy.py (pre-build hook).
# timeout_store.py looks it up at parents[2]/contracts/generation/, so it must
# live directly under /app inside the image.
COPY contracts/ ./contracts/

# VeFaaS (native/v1) 默认执行 /opt/application/run.sh 启动容器
RUN mkdir -p /opt/application && \
    printf '#!/bin/sh\nexec uvicorn src.main:app --host 0.0.0.0 --port 8000\n' \
    > /opt/application/run.sh && \
    chmod +x /opt/application/run.sh

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
