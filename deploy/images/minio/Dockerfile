# MinIO no longer publishes community images; build the pinned release from source.
# ADR-009: MinIO at a fixed version. Production object store is still open (architecture §16).
ARG MINIO_TAG=RELEASE.2025-10-15T17-29-55Z

FROM golang:1.24 AS build
ARG MINIO_TAG
RUN git clone --depth 1 --branch "${MINIO_TAG}" https://github.com/minio/minio.git /src
WORKDIR /src
RUN CGO_ENABLED=0 go build -trimpath -tags kqueue \
      -ldflags "-s -w -X github.com/minio/minio/cmd.ReleaseTag=${MINIO_TAG}" -o /out/minio .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/minio /usr/bin/minio
USER 65532:65532
EXPOSE 9000
ENTRYPOINT ["/usr/bin/minio"]
