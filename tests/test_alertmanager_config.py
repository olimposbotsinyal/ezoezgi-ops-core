"""infra/monitoring/alertmanager/alertmanager.yml -- secret sizintisi kontrolu.

Gercek Alertmanager gerektirmez -- yalnizca YAML'i parse edip icerigini
tarar.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ALERTMANAGER_CONFIG_PATH = Path("infra/monitoring/alertmanager/alertmanager.yml")

# Gercek bir secret gibi gorunen desenler -- API key/token benzeri uzun,
# rastgele karakter dizileri, bilinen provider onekleri (Slack, PagerDuty,
# generic Bearer token vb.). Placeholder'lar (${...} veya "" gibi) bu
# desenlerle eslesmez.
SUSPICIOUS_VALUE_PATTERNS = (
    re.compile(r"xox[baprs]-[0-9a-zA-Z-]+"),  # Slack token
    re.compile(r"[A-Za-z0-9_-]{32,}"),  # genel olarak uzun, rastgele-benzeri dize
)

ALLOWED_PLACEHOLDER_PATTERN = re.compile(r"^\$\{[A-Z0-9_]+\}$")


def _iter_string_values(data):
    if isinstance(data, dict):
        for v in data.values():
            yield from _iter_string_values(v)
    elif isinstance(data, list):
        for v in data:
            yield from _iter_string_values(v)
    elif isinstance(data, str):
        yield data


def test_alertmanager_config_is_valid_yaml():
    with ALERTMANAGER_CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert isinstance(data, dict)
    assert "route" in data
    assert "receivers" in data


def test_alertmanager_config_default_route_is_null_receiver():
    """Asama 1 (observe-only) -- varsayilan route hicbir yere gitmemeli."""
    with ALERTMANAGER_CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data["route"]["receiver"] == "null-receiver"

    null_receiver = next(r for r in data["receivers"] if r["name"] == "null-receiver")
    # null-receiver'in gercek bir bildirim kanali (webhook/email/slack)
    # OLMAMALI -- observe-only asamasinin garantisi budur.
    assert "webhook_configs" not in null_receiver
    assert "email_configs" not in null_receiver
    assert "slack_configs" not in null_receiver


def test_alertmanager_config_template_has_no_secrets():
    with ALERTMANAGER_CONFIG_PATH.open("r", encoding="utf-8") as f:
        raw_text = f.read()
        data = yaml.safe_load(raw_text)

    for value in _iter_string_values(data):
        if ALLOWED_PLACEHOLDER_PATTERN.match(value):
            continue  # ${ENV_VAR_NAME} bicimi -- bilerek izinli
        for pattern in SUSPICIOUS_VALUE_PATTERNS:
            assert not pattern.search(value), (
                f"Suphelu bir secret-benzeri deger bulundu: {value!r} "
                f"(desen: {pattern.pattern})"
            )

    # Webhook URL'leri, gercek bir domain/token icermeyen, yalnizca
    # ortam degiskeni placeholder'i olmali.
    for match in re.finditer(r"url:\s*(\S+)", raw_text):
        url_value = match.group(1).strip('"').strip("'")
        assert ALLOWED_PLACEHOLDER_PATTERN.match(url_value), (
            f"Webhook URL'i placeholder degil, gercek bir deger icerebilir: {url_value!r}"
        )
