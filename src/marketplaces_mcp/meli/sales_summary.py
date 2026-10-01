"""Estimate ticket promedio, facturación y desglose de medios de pago / cuotas
MSI a partir de las órdenes reales del vendedor.

Mercado Libre no expone por API las tasas ni sobretasas pactadas, ni los días
de liberación de fondos (son configuración comercial de la cuenta, no datos
de una orden) -- esos hay que sacarlos del panel de Mercado Pago. Lo que sí
se puede calcular agregando `/orders/search` es: ticket promedio, facturación
por periodo/mes, y el % de uso (en monto y en órdenes) por medio de pago y
por número de cuotas.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .client import MeliClient

_PAGE_SIZE = 50
_MAX_ORDERS = 5000  # salvaguarda para cuentas con mucho volumen

# Clasificación heurística de payment_method_id -> fila de la tabla pedida.
# Mercado Libre no marca explícitamente "tarjeta internacional" en la orden
# (dependería del BIN/issuer), así que esa fila queda fuera de lo calculable.
_DEBIT_PREFIXES = ("deb", "debvisa", "debmaster", "debcabal", "maestro")
_AMEX_IDS = ("amex",)


@dataclass
class SalesSummary:
    date_from: str
    date_to: str
    order_count: int = 0
    total_amount: float = 0.0
    currency: str | None = None
    monthly_totals: dict[str, float] = field(default_factory=dict)
    monthly_counts: dict[str, int] = field(default_factory=dict)
    payment_amount_by_bucket: dict[str, float] = field(default_factory=dict)
    payment_count_by_bucket: dict[str, int] = field(default_factory=dict)
    installments_amount: dict[int, float] = field(default_factory=dict)
    installments_count: dict[int, int] = field(default_factory=dict)
    truncated: bool = False

    @property
    def average_ticket(self) -> float:
        return self.total_amount / self.order_count if self.order_count else 0.0

    @property
    def months_covered(self) -> int:
        return max(len(self.monthly_totals), 1)

    @property
    def average_monthly_revenue(self) -> float:
        return self.total_amount / self.months_covered


def _bucket_for_payment(payment: dict) -> str:
    method_id = (payment.get("payment_method_id") or "").lower()
    payment_type = (payment.get("payment_type") or "").lower()

    if method_id in _AMEX_IDS or "amex" in method_id:
        return "AMEX"
    if payment_type == "account_money" or method_id == "account_money":
        return "Dinero en cuenta (Mercado Pago)"
    if payment_type in ("ticket", "atm", "bank_transfer"):
        return "Transferencia / efectivo"
    if method_id.startswith(_DEBIT_PREFIXES) or payment_type == "debit_card":
        return "Tarjeta de débito"
    if payment_type == "credit_card" or method_id.startswith(("vis", "mas", "nar")):
        return "Tarjeta de crédito"
    return f"Otro ({method_id or payment_type or 'desconocido'})"


def fetch_sales_summary(
    client: MeliClient,
    days: int = 30,
    status: str = "paid",
    on_progress: "callable | None" = None,
) -> SalesSummary:
    date_to = datetime.now(timezone.utc)
    date_from = date_to - timedelta(days=days)
    date_from_s = date_from.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")
    date_to_s = date_to.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")

    summary = SalesSummary(date_from=date_from_s, date_to=date_to_s)

    offset = 0
    total_results: int | None = None
    while True:
        page = client.list_orders(
            status=status,
            limit=_PAGE_SIZE,
            offset=offset,
            date_from=date_from_s,
            date_to=date_to_s,
        )
        results = page.get("results", [])
        if total_results is None:
            total_results = page.get("paging", {}).get("total", len(results))

        for order in results:
            amount = order.get("total_amount")
            if amount is None:
                continue
            summary.order_count += 1
            summary.total_amount += amount
            summary.currency = summary.currency or order.get("currency_id")

            closed = order.get("date_closed") or order.get("date_created") or ""
            month_key = closed[:7] if len(closed) >= 7 else "desconocido"
            summary.monthly_totals[month_key] = summary.monthly_totals.get(month_key, 0.0) + amount
            summary.monthly_counts[month_key] = summary.monthly_counts.get(month_key, 0) + 1

            for payment in order.get("payments", []) or []:
                if payment.get("status") != "approved":
                    continue
                pay_amount = payment.get("transaction_amount") or 0.0
                bucket = _bucket_for_payment(payment)
                summary.payment_amount_by_bucket[bucket] = (
                    summary.payment_amount_by_bucket.get(bucket, 0.0) + pay_amount
                )
                summary.payment_count_by_bucket[bucket] = (
                    summary.payment_count_by_bucket.get(bucket, 0) + 1
                )

                installments = payment.get("installments") or 1
                summary.installments_amount[installments] = (
                    summary.installments_amount.get(installments, 0.0) + pay_amount
                )
                summary.installments_count[installments] = (
                    summary.installments_count.get(installments, 0) + 1
                )

        if on_progress:
            on_progress(min(offset + len(results), total_results), total_results)

        offset += len(results)
        if not results or offset >= total_results or offset >= _MAX_ORDERS:
            summary.truncated = offset >= _MAX_ORDERS and offset < total_results
            break

    return summary


def _pct(part: float, whole: float) -> str:
    return f"{(part / whole * 100):.1f}%" if whole else "0.0%"


def print_summary(summary: SalesSummary) -> None:
    currency = summary.currency or "MXN"
    print(f"\nPeriodo analizado: {summary.date_from[:10]} a {summary.date_to[:10]}")
    print(f"Órdenes pagadas encontradas: {summary.order_count}")
    if summary.truncated:
        print("  (se alcanzó el límite de seguridad de órdenes procesadas; "
              "el resultado puede estar incompleto para este periodo)")

    print(f"\nTicket promedio: {summary.average_ticket:,.2f} {currency}")
    print(f"Facturación total del periodo: {summary.total_amount:,.2f} {currency}")
    print(f"Facturación mensual promedio: {summary.average_monthly_revenue:,.2f} {currency}")

    if summary.monthly_totals:
        print("\nFacturación por mes:")
        for month in sorted(summary.monthly_totals):
            print(
                f"  {month}: {summary.monthly_totals[month]:,.2f} {currency} "
                f"({summary.monthly_counts[month]} órdenes)"
            )

    total_paid_amount = sum(summary.payment_amount_by_bucket.values())
    if total_paid_amount:
        print("\nMedios de pago (% sobre monto pagado aprobado):")
        for bucket, amount in sorted(
            summary.payment_amount_by_bucket.items(), key=lambda kv: -kv[1]
        ):
            print(f"  {bucket}: {_pct(amount, total_paid_amount)}  "
                  f"({amount:,.2f} {currency}, {summary.payment_count_by_bucket[bucket]} pagos)")
        print("  (Mercado Libre no distingue tarjeta nacional vs. internacional en la orden;")
        print("   esa fila y las tasas/sobretasas hay que sacarlas del panel de Mercado Pago)")

    total_installment_amount = sum(summary.installments_amount.values())
    if total_installment_amount:
        print("\nCuotas (MSI) -- % sobre monto pagado aprobado:")
        for n in sorted(summary.installments_amount):
            label = "1x (sin cuotas)" if n == 1 else f"{n}x"
            amount = summary.installments_amount[n]
            print(f"  {label}: {_pct(amount, total_installment_amount)}  "
                  f"({amount:,.2f} {currency}, {summary.installments_count[n]} pagos)")

    if not summary.order_count:
        print("\nNo se encontraron órdenes pagadas en este periodo.")


def main() -> None:
    args = sys.argv[1:]
    days = 30
    for i, a in enumerate(args):
        if a == "--days" and i + 1 < len(args):
            days = int(args[i + 1])

    from .config import load_settings

    settings = load_settings()
    client = MeliClient(settings)
    try:
        def progress(done: int, total: int) -> None:
            if total and (done % 100 == 0 or done == total):
                print(f"  procesando órdenes: {done}/{total}")

        summary = fetch_sales_summary(client, days=days, on_progress=progress)
    finally:
        client.close()

    print_summary(summary)


if __name__ == "__main__":
    main()
