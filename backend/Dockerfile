FROM python:3.12-slim

# OpenCV (a docling/rapidocr dependency) needs these shared libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxcb1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY planlint ./planlint
# The docling extra is the accuracy-first codebook parser (layout-aware).
RUN uv pip install --system --no-cache .[docling]
# Pre-fetch Docling's layout/table models so the first upload doesn't stall
# on a multi-hundred-MB download at request time.
RUN docling-tools models download layout tableformer rapidocr

EXPOSE 8000
CMD ["uvicorn", "planlint.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
