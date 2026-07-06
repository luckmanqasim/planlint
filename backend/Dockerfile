FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY planlint ./planlint
RUN uv pip install --system --no-cache .

EXPOSE 8000
CMD ["uvicorn", "planlint.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
