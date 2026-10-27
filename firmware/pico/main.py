# Boot entry point. The application lives in app.py, which deploy.sh
# precompiles to app.mpy: compiling ~90 KB of source on the Pico at boot
# exhausts its heap (MemoryError), while loading bytecode does not.
import app
