FROM postgres:18-alpine

# Keep the existing Alpine PostgreSQL data directory compatible while adding pgvector.
RUN apk add --no-cache --virtual .pgvector-build build-base git \
    && git clone --branch v0.8.7 --depth 1 https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && make -C /tmp/pgvector OPTFLAGS="" with_llvm=no \
    && make -C /tmp/pgvector with_llvm=no install \
    && rm -rf /tmp/pgvector \
    && apk del .pgvector-build
