# syntax=docker/dockerfile:1

# Builder stage is intentionally minimal.
FROM python:3.12-alpine AS builder

WORKDIR /build

# Compile Python files to bytecode to catch syntax errors early.
COPY app/ ./app/
RUN python -m compileall app

# Runtime stage keeps the final image small.
FROM python:3.12-alpine AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=builder /build/app ./app

EXPOSE 8080

CMD ["python", "app/main.py"]
