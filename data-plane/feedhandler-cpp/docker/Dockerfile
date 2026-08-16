# Dockerfile - builds and runs the C++ feed-handler engine. Multi-stage:
# the build stage needs cmake/g++/make, the runtime stage doesn't - keeps
# the deployed image small and free of a compiler toolchain, same pattern
# as every other multi-stage build in this repo (control-api, web-ui).
FROM debian:bookworm-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake g++ make && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY . .
RUN rm -rf build && mkdir build && cd build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release && \
    cmake --build . -j"$(nproc)" && \
    ./feedhandler_tests

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY --from=build /src/build/feedhandler /usr/local/bin/feedhandler
COPY config/providers/ /etc/feedhandler/providers/

ENV FH_MODE=sim
ENV FH_STATUS_PORT=9200
EXPOSE 9200

ENTRYPOINT ["/usr/local/bin/feedhandler"]
