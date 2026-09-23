FROM ubuntu:24.04 AS base
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates swig tcl-dev zlib1g-dev libeigen3-dev \
        bison flex libfl-dev libboost-all-dev autoconf automake libtool \
    && rm -rf /var/lib/apt/lists/*

FROM base AS cudd
RUN git clone --depth 1 https://github.com/The-OpenROAD-Project/cudd.git /tmp/cudd \
    && cd /tmp/cudd \
    && autoreconf -i \
    && ./configure --prefix=/usr/local \
    && make -j 4 && make install \
    && ls -la /usr/local/lib /usr/local/include > /cudd-installed.txt

FROM cudd AS sta
RUN git clone --depth 1 https://github.com/parallaxsw/OpenSTA.git /tmp/sta \
    && cmake -S /tmp/sta -B /tmp/sta/build -DCMAKE_BUILD_TYPE=Release -DCUDD_DIR=/usr/local \
    && cmake --build /tmp/sta/build -j 4 \
    && cmake --install /tmp/sta/build \
    && rm -rf /tmp/sta \
    && sta -version
WORKDIR /work
