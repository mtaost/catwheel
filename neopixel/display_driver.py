import board
import neopixel
from adafruit_pixel_framebuf import PixelFramebuffer

pixel_pin = board.D18
width = 8
height = 8
pixels = neopixel.NeoPixel(pixel_pin, width * height, auto_write=False)

pixel_framebuf = PixelFramebuffer(
    pixels, width, height, brightness=0.5, rotation=0
)

# Draw shapes and text
pixel_framebuf.fill(0)  # Clear the display
pixel_framebuf.pixel(0, 0, 1)  # Turn on a single pixel
pixel_framebuf.line(0, 0, 7, 7, 1)  # Draw a line
pixel_framebuf.text("Hello", 0, 0, 1)  # Display text
pixel_framebuf.show()