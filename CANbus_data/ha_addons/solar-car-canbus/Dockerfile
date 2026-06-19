ARG BUILD_FROM
FROM $BUILD_FROM

# slcan uses a serial port (pyserial), not SocketCAN, so iproute2/can-utils
# aren't needed; can-utils kept only as a handy on-box debug tool.
RUN apk add --no-cache can-utils \
    && pip install --no-cache-dir python-can pyserial requests

COPY run.sh can_reader.py /
COPY solarcar_can /solarcar_can
RUN chmod a+x /run.sh

CMD ["/run.sh"]
