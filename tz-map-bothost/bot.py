"""Bothost entrypoint alias — platform may invoke bot.py; run the map server."""
import runpy

runpy.run_path("server.py", run_name="__main__")
