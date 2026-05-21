# Minimal container to test BESTGO battery CAN decoding on the HA OS box.
# Not a Home Assistant add-on -- a plain container run via `sudo docker`.
#
# Build (on the HA box, in the SSH add-on shell):
#   sudo docker build -t bestgo-test /share/bestgo-test
#
# Run -- real bus (needs host network + NET_ADMIN to bring up can0):
#   sudo docker run --rm --network host --cap-add NET_ADMIN bestgo-test 30
#
# Run -- dummy (no hardware, synthetic frames):
#   sudo docker run --rm bestgo-test -dummy 12
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir python-can==4.6.1

WORKDIR /app
COPY bestgo_logtest.py entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
