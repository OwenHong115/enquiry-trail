FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN pip install --no-cache-dir uv && uv sync --locked --no-dev
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH="/app"
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
