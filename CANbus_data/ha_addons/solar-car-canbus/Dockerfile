ARG BUILD_FROM
FROM $BUILD_FROM

# candlelight/gs_usb -> kernel SocketCAN can0: need iproute2 (ip link) to bring
# the interface up in run.sh; can-utils for on-box debug (candump).
RUN apk add --no-cache iproute2 can-utils \
    && pip install --no-cache-dir python-can requests

COPY run.sh can_reader.py /
COPY solarcar_can /solarcar_can
RUN chmod a+x /run.sh

CMD ["/run.sh"]
