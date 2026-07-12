ARG BUILD_FROM
FROM $BUILD_FROM

# Waveshare library pinned to an exact commit: an upstream push must never be
# able to break (or silently change) a routine rebuild, and patch.py's
# patterns are written against exactly this tree.
ARG EPAPER_COMMIT=86aa9932f471a50157cf02fafadc2c1b4a965449

RUN apk add --no-cache git gcc musl-dev linux-headers ttf-dejavu \
    && pip install --no-cache-dir spidev gpiod Pillow numpy requests qrcode \
    && git init -q /e-Paper \
    && git -C /e-Paper remote add origin https://github.com/waveshare/e-Paper.git \
    && git -C /e-Paper fetch -q --depth 1 origin $EPAPER_COMMIT \
    && git -C /e-Paper checkout -q FETCH_HEAD

# Patch the Waveshare library to use gpiod instead of gpiozero
COPY patch.py /patch.py
RUN python3 /patch.py

COPY run.sh *.py logo.png /
RUN chmod a+x /run.sh

CMD ["/run.sh"]
