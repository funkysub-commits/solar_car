filepath = '/e-Paper/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epdconfig.py'
with open(filepath) as f:
    c = f.read()

# Normalise trailing whitespace so the block replacements below match
# regardless of how the upstream Waveshare file is formatted.
c = '\n'.join(line.rstrip() for line in c.split('\n'))


def replace(text, old, new):
    """c.replace() that REFUSES to no-op: if upstream Waveshare restructures
    epdconfig.py and a pattern stops matching, the Docker build must fail
    here, loudly - not 'succeed' and then crash the add-on at runtime."""
    if old not in text:
        raise SystemExit(
            "patch.py: pattern not found in epdconfig.py - upstream layout "
            f"changed? Unmatched pattern starts with:\n{old.strip().splitlines()[0]}")
    return text.replace(old, new)


c = replace(c,
    """    def __init__(self):
        import spidev
        import gpiozero

        self.SPI = spidev.SpiDev()
        self.GPIO_RST_PIN    = gpiozero.LED(self.RST_PIN)
        self.GPIO_DC_PIN     = gpiozero.LED(self.DC_PIN)
        # self.GPIO_CS_PIN     = gpiozero.LED(self.CS_PIN)
        self.GPIO_PWR_PIN    = gpiozero.LED(self.PWR_PIN)
        self.GPIO_BUSY_PIN   = gpiozero.Button(self.BUSY_PIN, pull_up = False)""",
    """    def __init__(self):
        import spidev
        import gpiod
        from gpiod.line import Direction, Value
        self.gpiod_Value = Value
        self.SPI = spidev.SpiDev()
        self.request = gpiod.request_lines("/dev/gpiochip0", consumer="waveshare-epd", config={
            self.RST_PIN: gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.INACTIVE),
            self.DC_PIN: gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.INACTIVE),
            self.PWR_PIN: gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.INACTIVE),
            self.BUSY_PIN: gpiod.LineSettings(direction=Direction.INPUT),
        })""")

c = replace(c,
    """    def digital_write(self, pin, value):
        if pin == self.RST_PIN:
            if value:
                self.GPIO_RST_PIN.on()
            else:
                self.GPIO_RST_PIN.off()
        elif pin == self.DC_PIN:
            if value:
                self.GPIO_DC_PIN.on()
            else:
                self.GPIO_DC_PIN.off()
        # elif pin == self.CS_PIN:
        #     if value:
        #         self.GPIO_CS_PIN.on()
        #     else:
        #         self.GPIO_CS_PIN.off()
        elif pin == self.PWR_PIN:
            if value:
                self.GPIO_PWR_PIN.on()
            else:
                self.GPIO_PWR_PIN.off()""",
    """    def digital_write(self, pin, value):
        if pin == self.CS_PIN:
            return
        self.request.set_value(pin, self.gpiod_Value.ACTIVE if value else self.gpiod_Value.INACTIVE)""")

c = replace(c,
    """    def digital_read(self, pin):
        if pin == self.BUSY_PIN:
            return self.GPIO_BUSY_PIN.value
        elif pin == self.RST_PIN:
            return self.RST_PIN.value
        elif pin == self.DC_PIN:
            return self.DC_PIN.value
        # elif pin == self.CS_PIN:
        #     return self.CS_PIN.value
        elif pin == self.PWR_PIN:
            return self.PWR_PIN.value""",
    """    def digital_read(self, pin):
        return 1 if self.request.get_value(pin) == self.gpiod_Value.ACTIVE else 0""")

c = replace(c,
    '        self.GPIO_PWR_PIN.on()',
    '        self.request.set_value(self.PWR_PIN, self.gpiod_Value.ACTIVE)')

c = replace(c,'self.GPIO_RST_PIN.off()', 'self.request.set_value(self.RST_PIN, self.gpiod_Value.INACTIVE)')
c = replace(c,'self.GPIO_DC_PIN.off()', 'self.request.set_value(self.DC_PIN, self.gpiod_Value.INACTIVE)')
c = replace(c,'self.GPIO_PWR_PIN.off()', 'self.request.set_value(self.PWR_PIN, self.gpiod_Value.INACTIVE)')
c = replace(c,'self.GPIO_RST_PIN.close()', 'pass')
c = replace(c,'self.GPIO_DC_PIN.close()', 'pass')
c = replace(c,'self.GPIO_PWR_PIN.close()', 'pass')
c = replace(c,'self.GPIO_BUSY_PIN.close()', 'self.request.release()')

with open(filepath, 'w') as f:
    f.write(c)
print("All patches applied successfully")
