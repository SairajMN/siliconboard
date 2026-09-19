FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        yosys \
        verilator \
        iverilog \
        make \
        g++ \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
CMD ["bash"]
