FROM ros:lyrical-ros-base-resolute@sha256:dbb2a254523ee3c40ec9fc07956bc1042253c7beb3b6dad4a4585d85c99e9716 AS portaudio-build

ARG PORTAUDIO_VERSION=19.7.0
ARG PORTAUDIO_SHA256=5af29ba58bbdbb7bbcefaaecc77ec8fc413f0db6f4c4e286c40c3e1b83174fa0

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libasound2-dev=1.2.15.3-1ubuntu1.1 \
    && curl -fsSL \
        "https://github.com/PortAudio/portaudio/archive/refs/tags/v${PORTAUDIO_VERSION}.tar.gz" \
        -o /tmp/portaudio.tar.gz \
    && echo "${PORTAUDIO_SHA256}  /tmp/portaudio.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/portaudio.tar.gz -C /tmp \
    && cd "/tmp/portaudio-${PORTAUDIO_VERSION}" \
    && ./configure --prefix=/usr/local --with-alsa --without-jack --without-oss \
    && make -j2 \
    && make install

FROM ros:lyrical-ros-base-resolute@sha256:dbb2a254523ee3c40ec9fc07956bc1042253c7beb3b6dad4a4585d85c99e9716 AS core

ENV PATH="/opt/alice/venv/bin:${PATH}" \
    PYTHONPATH="/opt/alice/src" \
    ALICE_CONFIG_ROOT="/opt/alice/config" \
    ALICE_HARDWARE_ROOT="/opt/alice/hardware"

WORKDIR /opt/alice

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3-venv=3.14.3-0ubuntu2 \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv --system-site-packages /opt/alice/venv

COPY infra/ros2/requirements-core.txt /opt/alice/requirements-core.txt
RUN python -m pip install --no-cache-dir --requirement requirements-core.txt

COPY config /opt/alice/config
COPY hardware /opt/alice/hardware
COPY src /opt/alice/src
COPY --chmod=0555 infra/ros2/entrypoint.sh /opt/alice/entrypoint.sh

ENTRYPOINT ["/opt/alice/entrypoint.sh"]
CMD ["bash"]

FROM core AS speech

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libasound2t64=1.2.15.3-1ubuntu1.1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=portaudio-build /usr/local/lib/libportaudio.so* /usr/local/lib/
RUN ldconfig
COPY infra/ros2/requirements-speech.txt /opt/alice/requirements-speech.txt
RUN python -m pip install --no-cache-dir --requirement requirements-speech.txt

FROM core AS perception

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libasound2t64=1.2.15.3-1ubuntu1.1 \
        libegl1=1.7.0-3 \
        libgl1=1.7.0-3 \
        libgles2=1.7.0-3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=portaudio-build /usr/local/lib/libportaudio.so* /usr/local/lib/
RUN ldconfig
COPY infra/ros2/requirements-perception.txt /opt/alice/requirements-perception.txt
RUN python -m pip install --no-cache-dir --requirement requirements-perception.txt

FROM perception AS test

COPY infra/ros2/requirements-test.txt /opt/alice/requirements-test.txt
RUN python -m pip install --no-cache-dir --requirement requirements-test.txt \
    && python -m pip install --no-cache-dir uv==0.12.7
COPY pyproject.toml uv.lock /opt/alice/
COPY tests /opt/alice/tests
