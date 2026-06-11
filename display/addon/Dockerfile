ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache git gcc musl-dev linux-headers ttf-dejavu \
    && pip install --no-cache-dir spidev gpiod Pillow numpy requests \
    && git clone --depth 1 https://github.com/waveshare/e-Paper.git /e-Paper

# Patch the Waveshare library to use gpiod instead of gpiozero
COPY patch.py /patch.py
RUN python3 /patch.py

COPY run.sh *.py logo.png /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
