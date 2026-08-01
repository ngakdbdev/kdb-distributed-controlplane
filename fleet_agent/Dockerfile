# Minimal agent image. Runtime is stdlib-only Python; the backends shell out to
# `helm`/`kubectl` (k8s clouds) or `docker` (compose/on-prem), so those must be
# present in the image or mounted. This base covers the Helm path.
FROM alpine:3.20

RUN apk add --no-cache python3 helm kubectl

WORKDIR /app
COPY . /app/fleet_agent
# run as a package: python -m fleet_agent
ENV PYTHONPATH=/app
ENTRYPOINT ["python3", "-m", "fleet_agent"]
