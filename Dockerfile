FROM debian:bookworm-slim AS tailwind-builder
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
ARG TAILWIND_VERSION=v4.3.3
# The standalone Tailwind binary is glibc-linked, so this stage must be a glibc base
# (not Alpine/musl) or it fails to execute with a misleading "not found" error.
RUN curl -sL -o /usr/local/bin/tailwindcss \
      https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-x64 \
    && chmod +x /usr/local/bin/tailwindcss
WORKDIR /build
COPY app ./app
RUN tailwindcss -i ./app/static/css/input.css -o ./app/static/css/app.css --minify


FROM python:3.12-slim AS app
WORKDIR /app
ENV PYTHONUNBUFFERED=1

# Native libs required by WeasyPrint (invoice PDF rendering) for text shaping,
# rendering, and image decoding.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
      libffi-dev shared-mime-info fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

COPY --from=tailwind-builder /build/app/static/css/app.css ./app/static/css/app.css

RUN mkdir -p /app/data /app/static/uploads

EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
