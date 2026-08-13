"""Gercek uvicorn giris noktasi (PLAN.md T24) -- `python -m ops_suite.server`.

Varsayilan olarak yalnizca loopback'e (`127.0.0.1`) baglanir -- disaridan
erisim GEREKMEZ (v0, tek-kullanicili yerel kontrol merkezi). Port
`OPS_SUITE_PORT` ortam degiskeniyle degistirilebilir."""

from __future__ import annotations

import os

import uvicorn

from ops_suite.app import create_app

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8420

app = create_app()


def main() -> None:
    host = os.environ.get("OPS_SUITE_HOST", DEFAULT_HOST)
    port = int(os.environ.get("OPS_SUITE_PORT", str(DEFAULT_PORT)))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
