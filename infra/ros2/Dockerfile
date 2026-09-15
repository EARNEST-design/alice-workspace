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

FROM ros:lyrical-ros-base-resolute@sha256:dbb2a254523ee3c40ec9fc07956bc1042253c7beb3b6dad4a4585d85c99e9716 AS core-deps

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

FROM core-deps AS speech-deps

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libasound2t64=1.2.15.3-1ubuntu1.1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=portaudio-build /usr/local/lib/libportaudio.so.2.0.0 /usr/local/lib/
RUN ln -s libportaudio.so.2.0.0 /usr/local/lib/libportaudio.so.2 \
    && ln -s libportaudio.so.2 /usr/local/lib/libportaudio.so \
    && ldconfig
COPY infra/ros2/requirements-speech.txt /opt/alice/requirements-speech.txt
RUN python -m pip install --no-cache-dir --requirement requirements-speech.txt

FROM core-deps AS perception-deps

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libasound2t64=1.2.15.3-1ubuntu1.1 \
        libegl1=1.7.0-3 \
        libgl1=1.7.0-3 \
        libgles2=1.7.0-3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=portaudio-build /usr/local/lib/libportaudio.so.2.0.0 /usr/local/lib/
RUN ln -s libportaudio.so.2.0.0 /usr/local/lib/libportaudio.so.2 \
    && ln -s libportaudio.so.2 /usr/local/lib/libportaudio.so \
    && ldconfig
COPY infra/ros2/requirements-perception.txt /opt/alice/requirements-perception.txt
RUN python -m pip install --no-cache-dir --requirement requirements-perception.txt

FROM perception-deps AS test-deps

COPY infra/ros2/requirements-test.txt /opt/alice/requirements-test.txt
RUN python -m pip install --no-cache-dir --requirement requirements-test.txt \
    && python -m pip install --no-cache-dir uv==0.12.7

FROM core-deps AS ros-deps
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ros-lyrical-xacro=2.1.1-3resolute.20260728.204820 ros-lyrical-robot-state-publisher=3.5.5-3resolute.20260812.020221 ros-lyrical-joint-state-publisher=2.4.3-1resolute.20260812.024103 \
    ros-lyrical-urdfdom-py=1.2.1-4resolute.20260812.022205 psmisc=23.7-2ubuntu2 \
    && rm -rf /var/lib/apt/lists/*

FROM ros-deps AS app
COPY config /opt/alice/config
COPY hardware /opt/alice/hardware
COPY src /opt/alice/src
COPY ros2_ws/src /opt/alice/ros/src
COPY infra/ros2 /opt/alice/deployment
COPY infra/ros2/fastdds.xml infra/ros2/asound.conf infra/ros2/healthcheck.py /opt/alice/
COPY --chmod=0555 infra/ros2/node.py /opt/alice/venv/bin/alice-node
COPY --chmod=0555 infra/ros2/entrypoint.sh /opt/alice/entrypoint.sh
RUN python /opt/alice/deployment/source_manifest.py \
    && mkdir /fixtures && cp /opt/alice/config/speech/*.jsonl /fixtures/ \
    && . /opt/ros/lyrical/setup.sh \
    && python -m colcon --log-base /tmp/colcon-log build \
       --base-paths /opt/alice/ros/src --build-base /tmp/colcon-build \
       --install-base /opt/alice/ros/install \
    && head -1 /opt/alice/ros/install/alice_nodes/lib/alice_nodes/session | grep /opt/alice/venv/bin/python \
    && rm -rf /tmp/colcon-build /tmp/colcon-log
ENTRYPOINT ["/opt/alice/entrypoint.sh"]
CMD ["bash"]

FROM app AS core

FROM app AS speech
COPY --from=speech-deps /opt/alice/venv /opt/alice/venv
COPY --from=speech-deps /usr/local/lib/libportaudio* /usr/local/lib/
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libasound2t64=1.2.15.3-1ubuntu1.1 libasound2-plugins=1.2.12-2build1 pulseaudio-utils=1:17.0+dfsg1-2ubuntu4 \
    && rm -rf /var/lib/apt/lists/* && ldconfig
# The venv copy merges dependencies; restore the application wrapper explicitly.
COPY --chmod=0555 infra/ros2/node.py /opt/alice/venv/bin/alice-node

FROM app AS perception
COPY --from=perception-deps /opt/alice/venv /opt/alice/venv
COPY --from=perception-deps /usr/local/lib/libportaudio* /usr/local/lib/
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libasound2t64=1.2.15.3-1ubuntu1.1 libegl1=1.7.0-3 libgl1=1.7.0-3 libgles2=1.7.0-3 \
    && rm -rf /var/lib/apt/lists/* && ldconfig
COPY --chmod=0555 infra/ros2/node.py /opt/alice/venv/bin/alice-node

FROM perception AS test
COPY --from=test-deps /opt/alice/venv /opt/alice/venv
COPY pyproject.toml uv.lock /opt/alice/
COPY tests /opt/alice/tests
COPY --chmod=0555 infra/ros2/node.py /opt/alice/venv/bin/alice-node
