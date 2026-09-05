# Build from the repository root, not packages/ai-engine.
FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright PYTHONUNBUFFERED=1
COPY packages/ai-engine/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install --with-deps chromium
COPY packages/ai-engine/src ./src
COPY contracts ./contracts
EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
