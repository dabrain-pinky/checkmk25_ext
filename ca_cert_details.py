#!/usr/bin/env python3
"""Show certificate details next to the PEM content of trusted CAs

On the global setting "Trusted certificate authorities for SSL" every manually
added CA is only shown as raw PEM text while editing. This plug-in adds a panel
right of the text area which shows issuer, subject, validity and SHA256
fingerprint of each certificate contained in the PEM (a chain shows one block
per certificate).

The details are computed when the page is rendered, i.e. for newly pasted
content they appear after the setting has been saved (or the form has been
submitted once).
"""

import datetime
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes

from cmk.gui.htmllib.html import html
from cmk.gui.i18n import _
from cmk.gui.valuespec.definitions import _CAorCAChain

_PATCH_MARKER = "_ca_cert_details_patched"


def _pem_from_value(value: Any) -> bytes | None:
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, bytes):
        return value
    # Upload / fetch-from-server alternatives: the content is the last element
    if isinstance(value, tuple) and value and isinstance(value[-1], bytes):
        return value[-1]
    return None


def _load_certificates(value: Any) -> list[x509.Certificate]:
    if not (pem := _pem_from_value(value)) or b"-----BEGIN CERTIFICATE-----" not in pem:
        return []
    try:
        return x509.load_pem_x509_certificates(pem)
    except ValueError:
        return []


def _render_certificate(cert: x509.Certificate) -> None:
    not_before = cert.not_valid_before_utc
    not_after = cert.not_valid_after_utc
    now = datetime.datetime.now(tz=datetime.UTC)
    fingerprint = cert.fingerprint(hashes.SHA256()).hex(":").upper()

    html.open_table(class_="ca_cert_details", style="border-spacing:4px 2px;")
    for title, content, expired in [
        (_("Issuer"), cert.issuer.rfc4514_string(), False),
        (_("Subject"), cert.subject.rfc4514_string(), False),
        (_("Not before"), not_before.strftime("%Y-%m-%d %H:%M:%S UTC"), now < not_before),
        (_("Not after"), not_after.strftime("%Y-%m-%d %H:%M:%S UTC"), now > not_after),
        (_("Fingerprint (SHA256)"), fingerprint, False),
    ]:
        html.open_tr()
        html.td("%s:" % title, style="vertical-align:top;white-space:nowrap;font-weight:bold;")
        html.open_td(style="word-break:break-all;")
        html.span(content)
        if expired:
            html.span(" (%s)" % _("not valid"), class_="state2", style="font-weight:bold;")
        html.close_td()
        html.close_tr()
    html.close_table()


def _render_details(certs: list[x509.Certificate]) -> None:
    for nr, cert in enumerate(certs):
        if len(certs) > 1:
            html.div(
                _("Certificate %d of %d") % (nr + 1, len(certs)),
                style="font-style:italic;margin-top:%s;" % ("0" if nr == 0 else "8px"),
            )
        _render_certificate(cert)


def _patch() -> None:
    if getattr(_CAorCAChain, _PATCH_MARKER, False):
        return

    orig_render_input = _CAorCAChain.render_input

    def render_input(self: _CAorCAChain, varprefix: str, value: Any) -> None:
        certs = _load_certificates(value)
        if not certs:
            orig_render_input(self, varprefix, value)
            return

        html.open_table(class_="ca_cert_with_details")
        html.open_tr()
        html.open_td(style="vertical-align:top;")
        orig_render_input(self, varprefix, value)
        html.close_td()
        html.open_td(style="vertical-align:top;padding-left:16px;padding-top:22px;max-width:520px;")
        _render_details(certs)
        html.close_td()
        html.close_tr()
        html.close_table()

    _CAorCAChain.render_input = render_input  # type: ignore[method-assign]
    setattr(_CAorCAChain, _PATCH_MARKER, True)


_patch()
