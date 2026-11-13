"""Erreichbare TCP-Ports und HTTPS-Zertifikate vom Deck-Server aus prüfen."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import math
import socket
import ssl
import time
import warnings
from typing import Any
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from nodvard_sdk import ServiceCatalog

COMMON_PORTS = (
    21, 22, 23, 25, 53, 80, 110, 111, 135, 139, 143, 389, 443, 445, 465, 587, 631, 636,
    873, 993, 995, 1433, 2049, 2375, 2376, 3000, 3306, 3389, 5000, 5432, 5672, 5900,
    6379, 8000, 8006, 8080, 8443, 9000, 9090, 9200, 9443, 11211, 27017,
)
MAX_PORTS = 128
MAX_CERTIFICATES = 12
MAX_PARALLEL = 4
CONNECT_TIMEOUT_S = 1.5
TLS_TIMEOUT_S = 3.0
CATALOG_TIMEOUT_S = 20.0
HOST_TIMEOUT_S = 120.0
EXPIRY_WARNING_DAYS = 14


def _port(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        port = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return port if str(value).isdigit() and 0 < port <= 65535 else None


def _address(value: Any) -> str:
    return str(value or "").strip().strip("[]").lower().rstrip(".")


def _url_target(value: Any) -> tuple[str, int, bool] | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        return _address(parsed.hostname), parsed.port or (443 if parsed.scheme == "https" else 80), parsed.scheme == "https"
    except ValueError:
        return None


def _belongs_to(row: dict, host: Any) -> bool:
    if row.get("host_id"):
        return row["host_id"] == host.id
    if str(row.get("id") or "").startswith(f"{host.id}:"):
        return True
    return bool(row.get("host_address")) and _address(row["host_address"]) == _address(host.address)


async def _services(ctx: Any, host: Any) -> tuple[list[dict], list[str], bool]:
    rows: list[dict] = []
    notices: list[str] = []
    complete = True
    metadata = getattr(host, "metadata", {}) or {}
    for row in metadata.get("services", []) if isinstance(metadata.get("services"), list) else []:
        if isinstance(row, dict):
            rows.append(row)
    if metadata.get("url"):
        rows.append({"url": metadata["url"]})
    try:
        async with asyncio.timeout(CATALOG_TIMEOUT_S):
            providers = await ctx.capabilities.query(ServiceCatalog)
            for provider in providers:
                for row in await provider.list_services():
                    if not isinstance(row, dict) or not _belongs_to(row, host):
                        continue
                    if row.get("unreachable"):
                        complete = False
                        notices.append("Bekannte Dienste dieses Servers konnten nicht vollständig gelesen werden.")
                    else:
                        rows.append(row)
    except Exception:
        complete = False
        notices.append("Der Dienstkatalog ist gerade nicht erreichbar. Bekannte Dienste konnten nicht vollständig geprüft werden.")
    return rows, list(dict.fromkeys(notices)), complete


def _targets(host: Any, services: list[dict]) -> tuple[list[int], list[tuple[str, int]], list[str]]:
    ports = set(COMMON_PORTS)
    certificates: set[tuple[str, int]] = set()
    notices: list[str] = []
    credential_port = _port(getattr(host, "credential_port", None))
    if credential_port:
        ports.add(credential_port)
    for service in services:
        published = service.get("published_ports", [])
        if isinstance(published, list):
            ports.update(port for value in published if (port := _port(value)))
        if port := _port(service.get("port")):
            ports.add(port)
            if service.get("scheme") == "https" or service.get("protocol") == "https":
                certificates.add((_address(host.address), port))
        for key in ("url", "url_detected"):
            value = service.get(key)
            target = _url_target(value)
            if target is None:
                if value:
                    notices.append("Eine bekannte Dienstadresse ist ungültig und konnte nicht geprüft werden.")
                continue
            hostname, port, https = target
            if hostname in {_address(host.address), _address(getattr(host, "name", ""))}:
                ports.add(port)
            if https:
                certificates.add((hostname, port))
    if len(ports) > MAX_PORTS:
        notices.append(f"Die Portprüfung wurde auf {MAX_PORTS} Ports begrenzt.")
    if len(certificates) > MAX_CERTIFICATES:
        notices.append(f"Die Zertifikatsprüfung wurde auf {MAX_CERTIFICATES} Dienste begrenzt.")
    selected_ports = sorted(COMMON_PORTS) + sorted(ports - set(COMMON_PORTS))
    return sorted(selected_ports[:MAX_PORTS]), sorted(certificates)[:MAX_CERTIFICATES], notices


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(writer.wait_closed(), timeout=0.25)


async def _connect(address: str, port: int) -> str:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(address, port), CONNECT_TIMEOUT_S)
    except ConnectionRefusedError:
        return "closed"
    except TimeoutError:
        return "filtered"
    except OSError:
        return "unknown"
    await _close(writer)
    return "open"


async def _scan_ports(address: str, ports: list[int]) -> dict[int, str]:
    # Einmal auflösen, damit ein DNS-Ausfall nicht für jeden Port erneut wartet.
    answers = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(address, None, type=socket.SOCK_STREAM), CONNECT_TIMEOUT_S,
    )
    addresses = list(dict.fromkeys(answer[4][0] for answer in answers))[:2]
    if not addresses:
        raise OSError("Die Serveradresse konnte nicht aufgelöst werden.")
    semaphore = asyncio.Semaphore(MAX_PARALLEL)

    async def probe(port: int) -> tuple[int, str]:
        async with semaphore:
            states = [await _connect(target, port) for target in addresses]
            state = next((state for state in ("open", "unknown", "filtered") if state in states), "closed")
            return port, state

    return dict(await asyncio.gather(*(probe(port) for port in ports)))


async def _handshake(hostname: str, port: int, context: ssl.SSLContext) -> tuple[bytes, str | None]:
    _, writer = await asyncio.wait_for(
        asyncio.open_connection(hostname, port, ssl=context, server_hostname=hostname, ssl_handshake_timeout=TLS_TIMEOUT_S),
        TLS_TIMEOUT_S,
    )
    try:
        connection = writer.get_extra_info("ssl_object")
        if connection is None:
            raise OSError("Der Dienst hat kein TLS-Zertifikat geliefert.")
        return connection.getpeercert(binary_form=True), connection.version()
    finally:
        await _close(writer)


def _unverified_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _matches_hostname(certificate: x509.Certificate, hostname: str) -> bool:
    try:
        names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        hostname = hostname.encode("idna").decode("ascii").lower().rstrip(".")
        for value in names.get_values_for_type(x509.DNSName):
            name = value.lower().rstrip(".")
            if name == hostname:
                return True
            if name.startswith("*.") and name.count("*") == 1:
                if hostname.count(".") == name.count(".") and hostname.endswith(name[1:]):
                    return True
        return False
    return address in names.get_values_for_type(x509.IPAddress)


def _self_signed(certificate: x509.Certificate) -> bool | None:
    if certificate.issuer != certificate.subject:
        return False
    try:
        certificate.verify_directly_issued_by(certificate)
    except (ValueError, TypeError, InvalidSignature):
        return False
    except UnsupportedAlgorithm:
        return None
    return True


async def _weak_protocols(hostname: str, port: int) -> tuple[list[str], bool]:
    accepted: list[str] = []
    complete = True
    for version, label, supported in (
        (ssl.TLSVersion.TLSv1, "TLS 1.0", ssl.HAS_TLSv1),
        (ssl.TLSVersion.TLSv1_1, "TLS 1.1", ssl.HAS_TLSv1_1),
    ):
        if not supported:
            complete = False
            continue
        try:
            context = _unverified_context()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                context.minimum_version = context.maximum_version = version
            context.set_ciphers("ALL:@SECLEVEL=0")
            await _handshake(hostname, port, context)
            accepted.append(label)
        except ssl.SSLError as exc:
            # Nur die ausdrückliche Ablehnung der Version ist eine gesicherte Gegenprobe.
            if getattr(exc, "reason", None) != "TLSV1_ALERT_PROTOCOL_VERSION":
                complete = False
        except (OSError, TimeoutError, ValueError):
            complete = False
    return accepted, complete


async def _certificate(hostname: str, port: int, checked_at: float) -> dict:
    result: dict[str, Any] = {
        "hostname": hostname, "port": port, "status": "unavailable", "issuer": None,
        "expires_at": None, "days_remaining": None, "hostname_matches": None, "self_signed": None,
        "trusted": None, "protocol": None, "weak_protocols": [], "protocol_check_available": False,
        "warnings": [], "error": None,
    }
    try:
        try:
            der, protocol = await _handshake(hostname, port, ssl.create_default_context())
            result["trusted"] = True
        except ssl.SSLCertVerificationError:
            der, protocol = await _handshake(hostname, port, _unverified_context())
            result["trusted"] = False
        certificate = x509.load_der_x509_certificate(der)
        expires_at = certificate.not_valid_after_utc.timestamp()
        result.update(
            issuer=certificate.issuer.rfc4514_string(), expires_at=expires_at,
            days_remaining=math.floor((expires_at - checked_at) / 86400), protocol=protocol,
            hostname_matches=_matches_hostname(certificate, hostname), self_signed=_self_signed(certificate),
        )
        if expires_at < checked_at:
            result["warnings"].append("Das Zertifikat ist abgelaufen.")
        elif expires_at - checked_at < EXPIRY_WARNING_DAYS * 86400:
            result["warnings"].append("Das Zertifikat läuft in weniger als 14 Tagen ab.")
        if result["hostname_matches"] is False:
            result["warnings"].append("Das Zertifikat passt nicht zum Namen oder zur IP-Adresse dieses Dienstes.")
        if result["self_signed"]:
            result["warnings"].append(
                "Das Zertifikat ist selbstsigniert. Das ist im Heimnetz üblich; die Identität des Servers "
                "wird dadurch jedoch nicht von einer unabhängigen Zertifizierungsstelle bestätigt."
            )
        elif result["trusted"] is False:
            result["warnings"].append("Die Zertifikatskette ist für Deck nicht vertrauenswürdig.")
        result["status"] = "ok"
    except (OSError, TimeoutError, ValueError, UnsupportedAlgorithm):
        result["error"] = "Das Zertifikat konnte nicht gelesen werden. Der Dienst ist nicht erreichbar oder unterstützt kein passendes TLS."
    weak, available = await _weak_protocols(hostname, port)
    result["weak_protocols"] = weak
    result["protocol_check_available"] = available
    if weak:
        result["warnings"].append(f"Der Dienst erlaubt veraltete Verschlüsselung: {', '.join(weak)}.")
    if not available:
        result["warnings"].append("TLS 1.0 und TLS 1.1 konnten nicht vollständig geprüft werden. Das ist keine Entwarnung.")
    if result["status"] == "ok" and result["warnings"]:
        result["status"] = "warning"
    return result


async def check_host(ctx: Any, host_id: str, previous: dict | None = None) -> dict:
    result: dict[str, Any] = {
        "status": "unavailable", "checked_at": time.time(), "open_ports": [], "new_ports": [],
        "certificates": [], "warnings": [], "error": None, "checked_ports": [], "address": None,
    }
    try:
        async with asyncio.timeout(HOST_TIMEOUT_S):
            host = await ctx.hosts.get(host_id)
            if host is None or not _address(getattr(host, "address", None)):
                result["error"] = "Der Server hat keine erreichbare Adresse oder wurde entfernt."
                return result
            services, notices, catalog_complete = await _services(ctx, host)
            ports, targets, limits = _targets(host, services)
            result["address"] = _address(host.address)
            result["checked_ports"] = ports
            result["warnings"].extend(notices + limits)
            states = await _scan_ports(_address(host.address), ports)
            result["open_ports"] = sorted(port for port, state in states.items() if state == "open")
            unknown = sum(state == "unknown" for state in states.values())
            filtered = sum(state == "filtered" for state in states.values())
            if unknown:
                result["warnings"].append(f"{unknown} Ports haben nicht eindeutig geantwortet; sie gelten nicht als geschlossen.")
            if filtered:
                result["warnings"].append(
                    f"{filtered} Ports haben nicht innerhalb der Frist geantwortet. Eine Firewall kann sie filtern; "
                    "sie waren für Deck nicht erreichbar und gelten nicht als sicher geschlossen."
                )
            if (
                previous and previous.get("status") in {"ok", "warning"}
                and previous.get("address", result["address"]) == result["address"]
            ):
                known = set(previous.get("open_ports", []))
                compared = set(previous.get("checked_ports", ports))
                result["new_ports"] = sorted((set(result["open_ports"]) - known) & compared)
            if result["new_ports"]:
                result["warnings"].append("Neu erreichbar seit der letzten Prüfung: " + ", ".join(map(str, result["new_ports"])) + ".")
            semaphore = asyncio.Semaphore(2)

            async def certificate(target: tuple[str, int]) -> dict:
                async with semaphore:
                    return await _certificate(*target, result["checked_at"])

            result["certificates"] = await asyncio.gather(*(certificate(target) for target in targets))
            reachable = any(state in {"open", "closed"} for state in states.values())
            incomplete = unknown or (filtered and not reachable) or limits or not catalog_complete or any(
                cert["status"] == "unavailable" for cert in result["certificates"]
            )
            if incomplete:
                result["status"] = "unavailable"
                result["error"] = "Die Außenprüfung war unvollständig. Der letzte vollständige Vergleichsstand bleibt erhalten."
            elif result["warnings"] or any(cert["status"] == "warning" for cert in result["certificates"]):
                result["status"] = "warning"
            else:
                result["status"] = "ok"
    except (OSError, TimeoutError):
        result["error"] = "Der Server konnte von Deck aus nicht vollständig geprüft werden. Bitte später erneut versuchen."
    except Exception:
        result["status"] = "error"
        result["error"] = "Die Außenprüfung konnte nicht abgeschlossen werden. Bitte später erneut versuchen."
    return result
