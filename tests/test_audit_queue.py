"""Cola no bloqueante de auditoria HTTP (sin base de datos).

Verifica que:

- el camino de respuesta no espera a la persistencia;
- la cola acotada descarta eventos (con log) cuando esta llena;
- el worker reintenta con backoff;
- el drenaje en shutdown vacia lo pendiente.
"""

from __future__ import annotations

import asyncio

import pytest

from src.middleware import audit as audit_mw


@pytest.fixture(autouse=True)
async def _drain_after_test():
    yield
    await audit_mw.drain_audit_queue(timeout=1.0)


@pytest.mark.asyncio
async def test_schedule_does_not_wait_for_persistence(monkeypatch):
    monkeypatch.setenv("AUDIT_SYNC_WRITES", "false")
    entered = asyncio.Event()
    release = asyncio.Event()
    persisted: list[dict] = []

    async def slow_persist(data: dict) -> None:
        entered.set()
        await release.wait()
        persisted.append(data)

    monkeypatch.setattr(audit_mw, "_persist_http_audit", slow_persist)

    # Debe retornar aunque la escritura siga bloqueada.
    await asyncio.wait_for(audit_mw._schedule_audit({"seq": 1}), timeout=0.5)
    await asyncio.wait_for(entered.wait(), timeout=0.5)

    assert persisted == []
    release.set()
    await audit_mw.drain_audit_queue(timeout=1.0)
    assert persisted == [{"seq": 1}]


@pytest.mark.asyncio
async def test_full_queue_drops_instead_of_blocking(monkeypatch, caplog):
    monkeypatch.setenv("AUDIT_SYNC_WRITES", "false")
    monkeypatch.setenv("AUDIT_QUEUE_MAXSIZE", "1")
    entered = asyncio.Event()
    release = asyncio.Event()
    persisted: list[dict] = []

    async def slow_persist(data: dict) -> None:
        entered.set()
        await release.wait()
        persisted.append(data)

    monkeypatch.setattr(audit_mw, "_persist_http_audit", slow_persist)

    await audit_mw._schedule_audit({"seq": 1})
    await asyncio.wait_for(entered.wait(), timeout=0.5)

    # El worker esta ocupado; la cola admite uno y el siguiente se descarta.
    await audit_mw._schedule_audit({"seq": 2})
    await audit_mw._schedule_audit({"seq": 3})

    assert "Cola de auditoria llena" in caplog.text

    release.set()
    await audit_mw.drain_audit_queue(timeout=1.0)
    assert sorted(item["seq"] for item in persisted) == [1, 2]


@pytest.mark.asyncio
async def test_write_with_retry_retries_and_succeeds(monkeypatch):
    monkeypatch.setenv("AUDIT_WRITE_RETRIES", "2")
    monkeypatch.setenv("AUDIT_RETRY_BACKOFF_MS", "0")
    attempts: list[int] = []

    async def flaky(data: dict) -> None:
        attempts.append(data["seq"])
        if len(attempts) < 3:
            raise RuntimeError("fallo transitorio")

    monkeypatch.setattr(audit_mw, "_persist_http_audit", flaky)

    assert await audit_mw._write_with_retry({"seq": 7}) is True
    assert attempts == [7, 7, 7]


@pytest.mark.asyncio
async def test_batch_write_uses_single_commit(monkeypatch):
    monkeypatch.setenv("AUDIT_WRITE_RETRIES", "0")
    batches: list[list[dict]] = []

    async def persist_batch(batch: list[dict]) -> None:
        batches.append(batch)

    monkeypatch.setattr(audit_mw, "_persist_http_audit_batch", persist_batch)

    await audit_mw._write_batch_with_retry([{"seq": 1}, {"seq": 2}, {"seq": 3}])
    assert batches == [[{"seq": 1}, {"seq": 2}, {"seq": 3}]]


@pytest.mark.asyncio
async def test_batch_failure_falls_back_to_row_by_row(monkeypatch, caplog):
    monkeypatch.setenv("AUDIT_WRITE_RETRIES", "0")
    monkeypatch.setenv("AUDIT_RETRY_BACKOFF_MS", "0")
    rows: list[dict] = []

    async def failing_batch(batch: list[dict]) -> None:
        raise RuntimeError("fallo de lote")

    async def persist_one(data: dict) -> None:
        rows.append(data)

    monkeypatch.setattr(
        audit_mw, "_persist_http_audit_batch", failing_batch
    )
    monkeypatch.setattr(audit_mw, "_persist_http_audit", persist_one)

    await audit_mw._write_batch_with_retry([{"seq": 1}, {"seq": 2}])
    assert rows == [{"seq": 1}, {"seq": 2}]
    assert "se reintenta fila por fila" in caplog.text


@pytest.mark.asyncio
async def test_write_with_retry_gives_up_without_raising(monkeypatch, caplog):
    monkeypatch.setenv("AUDIT_WRITE_RETRIES", "1")
    monkeypatch.setenv("AUDIT_RETRY_BACKOFF_MS", "0")

    async def always_fail(data: dict) -> None:
        raise RuntimeError("base caida")

    monkeypatch.setattr(audit_mw, "_persist_http_audit", always_fail)

    assert await audit_mw._write_with_retry({"seq": 1}) is False
    assert "No se pudo persistir auditoria HTTP tras 2 intentos" in caplog.text
    # El detalle sensible del error no se registra.
    assert "base caida" not in caplog.text


def test_sync_mode_default_is_off(monkeypatch):
    monkeypatch.delenv("AUDIT_SYNC_WRITES", raising=False)
    assert audit_mw._sync_writes() is False

    monkeypatch.setenv("AUDIT_SYNC_WRITES", "true")
    assert audit_mw._sync_writes() is True
