# Multi-stage · final image is ~12 MB (distroless static · no shell · no apk)
FROM golang:1.25-alpine AS build

WORKDIR /src

# 1. deps layer (none here — pure stdlib — but keep the pattern for future-proofing)
COPY go.mod ./
RUN go mod download

# 2. source
COPY agent ./agent
COPY cmd ./cmd

# 3. static binary · no CGO so it runs on distroless/static
RUN CGO_ENABLED=0 GOOS=linux go build \
    -trimpath \
    -ldflags="-s -w" \
    -o /out/gateway ./cmd/example

# ---- final ----
FROM gcr.io/distroless/static-debian12:nonroot

WORKDIR /
COPY --from=build /out/gateway /gateway

USER nonroot:nonroot
EXPOSE 8080

# distroless has no shell · use exec form · the binary's `-mode=http-server`
# runs the proxy: GET /health · POST /api/ask · GET /api/stream
ENTRYPOINT ["/gateway", "-mode=http-server"]
