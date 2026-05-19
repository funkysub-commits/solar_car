ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache iproute2 can-utils \
    && pip install --no-cache-dir python-can requests

COPY run.sh can_reader.py /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
