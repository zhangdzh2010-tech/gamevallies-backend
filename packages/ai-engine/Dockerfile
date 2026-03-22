FROM python:3.12-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install --with-deps chromium

COPY src/ ./src/

# VeFaaS (native/v1) 默认执行 /opt/application/run.sh 启动容器
RUN mkdir -p /opt/application && \
    printf '#!/bin/sh\nexec uvicorn src.main:app --host 0.0.0.0 --port 8000\n' \
    > /opt/application/run.sh && \
    chmod +x /opt/application/run.sh

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
