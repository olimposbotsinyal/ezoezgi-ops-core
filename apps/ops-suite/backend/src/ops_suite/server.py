"""Gercek uvicorn giris noktasi (PLAN.md T24/T28) -- `python -m ops_suite.server`.

Varsayilan olarak yalnizca loopback'e (`127.0.0.1`) baglanir -- disaridan
erisim GEREKMEZ (v0, tek-kullanicili yerel kontrol merkezi). Port
`OPS_SUITE_PORT` ortam degiskeniyle degistirilebilir.

`OPS_SUITE_IDENTITY_CONFIG_PATH` (opsiyonel) -- `identity.py::IdentityStore`
icin `config/ops_suite_identities.json` disinda bir dosya kullanmak
gerektiginde (ornegin `scripts/ops_suite_demo.py`'nin gecici/demo-amacli
owner+delegate kimlikleriyle GERCEK bearer-token akisini uctan uca
kanitlamasi icin) -- bkz. BACKLOG.md B044."""

from __future__ import annotations

import os

import uvicorn

from ops_suite.app import create_app
from ops_suite.identity import DEFAULT_IDENTITY_CONFIG_PATH, IdentityStore

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8420

_identity_config_path = os.environ.get("OPS_SUITE_IDENTITY_CONFIG_PATH", str(DEFAULT_IDENTITY_CONFIG_PATH))
app = create_app(identity_store=IdentityStore.from_config_path(_identity_config_path))


def main() -> None:
    host = os.environ.get("OPS_SUITE_HOST", DEFAULT_HOST)
    port = int(os.environ.get("OPS_SUITE_PORT", str(DEFAULT_PORT)))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
