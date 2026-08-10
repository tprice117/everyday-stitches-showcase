
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.db import transaction, IntegrityError, DataError
from django.db.models import Q, Sum, Count, Max, F, Value, DecimalField, ExpressionWrapper, DateField
from django.db.models.functions import TruncMonth, Coalesce
from .models import (Pattern, Customer, Order, LineItem, SalesChannel,
                     PrintItem, PatternUrl, PrintJob, PrintJobStatus, PrintJobLineItem,
                     ImportAudit)
from .forms import (NewPOForm, EditPOForm, AddPatternForm, AddCustomerForm,
                    AddExpenseForm, NewPrintJobForm, EditPrintJobForm)
import datetime
import html as html_lib
import os
import re
from decimal import Decimal
from django.shortcuts import get_object_or_404
from django.http import HttpResponse
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
#from django.http import HttpResponse



# `line_total`
def _revenue_expr():
    est_revenue = ExpressionWrapper(
        F('quantity') * F('pattern__retail_price')
        * Coalesce(F('order__customer__pct_of_retail'), Value(100)) / Value(100),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    return Coalesce(
        F('line_total'), est_revenue,
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )


def _sales_window(months=12):
    """Return (start_date, anchor_date, [month_first_days]) for the trailing
    `months` window, anchored to the most recent order date in the database
    (not today) so migrated/historical data still charts something. Returns
    (None, None, []) when there are no orders."""
    anchor = Order.objects.aggregate(m=Max('date_ordered'))['m']
    if not anchor:
        return None, None, []

    year, month = anchor.year, anchor.month - (months - 1)
    while month <= 0:
        month += 12
        year -= 1
    start = datetime.date(year, month, 1)

    buckets = []
    y, mo = start.year, start.month
    for _ in range(months):
        buckets.append(datetime.date(y, mo, 1))
        mo += 1
        if mo > 12:
            mo, y = 1, y + 1
    return start, anchor, buckets


def _monthly_sales(months=12):
    """Revenue and order counts per month. Months with no orders are filled
    with zeros so the series is always continuous."""
    start, anchor, buckets = _sales_window(months)
    if not buckets:
        return []

    rows = (LineItem.objects
            .filter(order__date_ordered__gte=start,
                    order__date_ordered__lte=anchor)
            .annotate(m=TruncMonth('order__date_ordered'))
            .values('m')
            .annotate(revenue=Sum(_revenue_expr()),
                      order_count=Count('order', distinct=True))
            .order_by('m'))
    by_month = {r['m']: r for r in rows}

    series = []
    for key in buckets:
        row = by_month.get(key)
        series.append({
            'label': key.strftime('%b %Y'),
            'revenue': float(row['revenue']) if row and row['revenue'] else 0.0,
            'orders': row['order_count'] if row else 0,
        })
    return series


def _monthly_sales_by_channel(months=12):
    """Monthly sales split by sales channel, expressed as each channel's
    percentage of that month's total revenue. Returns {labels, series} where
    each series is {name, data} — one line per channel that has revenue in the
    window, ordered by total revenue (largest first). Percentages in a given
    month sum to ~100 (0 for months with no sales)."""
    start, anchor, buckets = _sales_window(months)
    if not buckets:
        return {'labels': [], 'series': []}

    rows = (LineItem.objects
            .filter(order__date_ordered__gte=start,
                    order__date_ordered__lte=anchor)
            .annotate(m=TruncMonth('order__date_ordered'))
            .values('m', 'order__source_id', 'order__source__name')
            .annotate(revenue=Sum(_revenue_expr()))
            .order_by('m'))

    # channel_id -> {name, totals: {month_key: revenue}}
    channels = {}
    month_totals = {key: 0.0 for key in buckets}
    for r in rows:
        cid = r['order__source_id']
        name = r['order__source__name'] or (cid or 'Unknown')
        rev = float(r['revenue'] or 0.0)
        ch = channels.setdefault(cid, {'name': name, 'totals': {}})
        ch['totals'][r['m']] = rev
        if r['m'] in month_totals:
            month_totals[r['m']] += rev

    ordered = sorted(channels.values(),
                     key=lambda c: sum(c['totals'].values()), reverse=True)
    series = []
    for c in ordered:
        data = []
        for key in buckets:
            total = month_totals.get(key, 0.0)
            pct = (c['totals'].get(key, 0.0) / total * 100) if total else 0.0
            data.append(round(pct, 1))
        series.append({'name': c['name'], 'data': data})

    return {
        'labels': [key.strftime('%b %Y') for key in buckets],
        'series': series,
    }


def _top_patterns_split_by_channel(metric_factory, months=12, limit=10):
    """Top `limit` patterns by `metric_factory()` (a Sum() aggregate) over the
    trailing window, each pattern's metric split by sales channel. Returns
    {labels, series} where `series` is one entry per channel {name, data} aligned
    to `labels` — for a stacked horizontal bar chart. Channels are ordered by
    total contribution (largest first). `metric_factory` is a callable returning
    a fresh aggregate so it can be used in two separate querysets."""
    start, anchor, buckets = _sales_window(months)
    if not buckets:
        return {'labels': [], 'series': []}

    # Only true patterns (type='P', which includes notions and templates) belong
    # in the pattern rankings — resale items like rulers are excluded so they
    # don't crowd out patterns. Their revenue is still on line_items and totals.
    base = LineItem.objects.filter(order__date_ordered__gte=start,
                                   order__date_ordered__lte=anchor,
                                   pattern__type=Pattern.PATTERN)

    # The top patterns by total metric.
    top = (base.values('pattern_id', 'pattern__sku', 'pattern__pattern_name')
           .annotate(metric=metric_factory())
           .order_by('-metric')[:limit])
    pattern_ids = [r['pattern_id'] for r in top]
    index = {pid: i for i, pid in enumerate(pattern_ids)}
    labels = []
    for r in top:
        sku = r['pattern__sku'] or ''
        name = r['pattern__pattern_name'] or sku or 'Unknown'
        labels.append((f'{sku} — {name}' if sku else name)[:40])

    # Split each of those patterns' metric by sales channel.
    rows = (base.filter(pattern_id__in=pattern_ids)
            .values('pattern_id', 'order__source_id', 'order__source__name')
            .annotate(metric=metric_factory()))

    channels = {}  # channel_id -> {name, data: [metric per pattern]}
    for r in rows:
        i = index.get(r['pattern_id'])
        if i is None:
            continue
        cid = r['order__source_id']
        ch = channels.setdefault(cid, {
            'name': r['order__source__name'] or (cid or 'Unknown'),
            'data': [0.0] * len(labels),
        })
        ch['data'][i] += float(r['metric'] or 0.0)

    ordered = sorted(channels.values(), key=lambda c: sum(c['data']), reverse=True)
    return {'labels': labels, 'series': ordered}


def _top_patterns_by_revenue(months=12, limit=10):
    """Top patterns by estimated revenue, split by channel (stacked bar)."""
    data = _top_patterns_split_by_channel(lambda: Sum(_revenue_expr()), months, limit)
    for c in data['series']:
        c['data'] = [round(v, 2) for v in c['data']]
    return data


def _top_patterns_by_count(months=12, limit=10):
    """Top patterns by units sold (sum of quantity), split by channel (stacked
    bar). Same shape as _top_patterns_by_revenue so both share one chart builder."""
    data = _top_patterns_split_by_channel(lambda: Sum('quantity'), months, limit)
    for c in data['series']:
        c['data'] = [int(round(v)) for v in c['data']]
    return data


@login_required
def indexPageView(request):
    low_patterns = Pattern.objects.filter(inventory__gt=0, inventory__lt=50).order_by('inventory')

    series = _monthly_sales(12)
    total_revenue = sum(s['revenue'] for s in series)
    total_orders = sum(s['orders'] for s in series)
    avg_order = (total_revenue / total_orders) if total_orders else 0
    chart_data = {
        'labels': [s['label'] for s in series],
        'revenue': [round(s['revenue'], 2) for s in series],
        'orders': [s['orders'] for s in series],
    }
    channel_data = _monthly_sales_by_channel(12)
    top_patterns_data = _top_patterns_by_revenue(12, 10)
    top_count_data = _top_patterns_by_count(12, 10)
    inventory_rows = Pattern.objects.all().order_by('pattern_name')

    # Print jobs sitting in a status flagged as an alert get a dashboard banner,
    # summarised as a count per status rather than one line per job.
    print_alerts = [
        {'status': row['status__description'], 'count': row['count']}
        for row in (PrintJob.objects
                    .filter(status__alert=True)
                    .values('status__sort_order', 'status__description')
                    .annotate(count=Count('print_job_id'))
                    .order_by('status__sort_order'))
    ]

    return render(request, 'dashboard/index.html', {
        'patterns': low_patterns,
        'print_alerts': print_alerts,
        'chart_data': chart_data,
        'channel_data': channel_data,
        'top_patterns_data': top_patterns_data,
        'top_count_data': top_count_data,
        'inventory_rows': inventory_rows,
        'total_revenue': total_revenue,
        'total_orders': total_orders,
        'avg_order': avg_order,
        'this_month_revenue': series[-1]['revenue'] if series else 0,
        'this_month_label': series[-1]['label'] if series else '',
    })

@login_required
def newPOView(request):
    if request.method == 'POST':
        form = NewPOForm(request.POST)
        if form.is_valid():
            order = form.save()
            return redirect('success', invoice_id=order.order_id)
    else:
        form = NewPOForm()

    # Map each customer -> their sales channel, from existing orders, falling
    # back to the customer's `type` (which matches a sales_channel id).
    channel_map = {}
    for cid, src in (Order.objects
                     .exclude(customer=None).exclude(source=None)
                     .order_by('order_id')
                     .values_list('customer_id', 'source_id')):
        channel_map[cid] = src
    valid_channels = set(SalesChannel.objects.values_list('id', flat=True))

    # Collapse every customer into one of the two selectable channels so the
    # customer dropdown can be narrowed by the chosen channel: 'D' (Distributor)
    # if their history/type is Distributor, else 'X' (Other). This guarantees
    # every customer is reachable under one of the two options.
    bucket_map = {}
    for c in Customer.objects.all():
        ch = channel_map.get(c.customer_id) or (c.type if c.type in valid_channels else None)
        bucket_map[c.customer_id] = 'D' if ch == 'D' else 'X'

    return render(request, 'dashboard/newPO.html', {
        'form': form,
        'customer_bucket_map': bucket_map,
    })

@login_required
def successView(request, invoice_id):
    invoice = get_object_or_404(Order, order_id=invoice_id)
    return render(request, 'dashboard/success.html', {
        'invoice': invoice,
        'invoice_filename': _doc_filename('INV', invoice),
    })

# ── Dashboard action stubs (to be built out one at a time) ─────────────────
@login_required
def addExpenseView(request):
    if request.method == 'POST':
        form = AddExpenseForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    exp = form.save()
                return redirect(f"{reverse('addExpense')}?saved={exp.expense_id}")
            except (IntegrityError, DataError) as exc:
                form.add_error(None, f"Could not save: {exc}")
    else:
        form = AddExpenseForm()
    return render(request, 'dashboard/addExpense.html',
                  {'form': form, 'saved': request.GET.get('saved')})

@login_required
def modifyInventoryView(request):
    if request.method == 'POST':
        updated = 0
        pids = [k[4:] for k in request.POST if k.startswith('inv_')]
        for pid in pids:
            # An adjustment (+/-) takes precedence: add it to the current count
            # atomically so concurrent edits don't clobber each other.
            adj_raw = (request.POST.get(f'adj_{pid}') or '').strip()
            if adj_raw:
                try:
                    delta = int(adj_raw)
                except ValueError:
                    delta = 0
                if delta:
                    updated += (Pattern.objects.filter(pattern_id=pid)
                                .update(inventory=F('inventory') + delta))
                    continue
            # Otherwise set the exact inventory value, if it changed.
            try:
                new_val = int(request.POST.get(f'inv_{pid}'))
            except (ValueError, TypeError):
                continue
            updated += (Pattern.objects.filter(pattern_id=pid)
                        .exclude(inventory=new_val)
                        .update(inventory=new_val))
        return redirect(f"{reverse('modifyInventory')}?updated={updated}")

    patterns = Pattern.objects.all().order_by('sku')
    query = request.GET.get('q', '').strip()
    if query:
        patterns = patterns.filter(Q(sku__icontains=query) | Q(pattern_name__icontains=query))
    return render(request, 'dashboard/modifyInventory.html', {
        'patterns': patterns,
        'query': query,
        'updated': request.GET.get('updated'),
    })

@login_required
def markPOPaidView(request):
    if request.method == 'POST':
        ids = request.POST.getlist('order_ids')
        count = 0
        if ids:
            count = (Order.objects.filter(order_id__in=ids)
                     .update(paid=True, payment_date=datetime.date.today()))
        return redirect(f"{reverse('markPOPaid')}?marked={count}")

    orders = (Order.objects.filter(paid=False)
              .select_related('customer')
              .annotate(due_date=ExpressionWrapper(
                  F('date_invoiced') + datetime.timedelta(days=30),
                  output_field=DateField()))
              .order_by('-order_id'))
    query = request.GET.get('q', '').strip()
    if query:
        orders = orders.filter(customers_po_number__icontains=query)
    return render(request, 'dashboard/markPOPaid.html', {
        'orders': orders[:200],
        'query': query,
        'marked': request.GET.get('marked'),
    })

@login_required
def editPOListView(request):
    orders = (Order.objects
              .select_related('customer')
              .order_by('-order_id'))
    query = request.GET.get('q', '').strip()
    if query:
        orders = orders.filter(customers_po_number__icontains=query)
    return render(request, 'dashboard/editPO_list.html',
                  {'orders': orders[:200], 'query': query})

@login_required
def editPOView(request, order_id):
    order = get_object_or_404(Order, order_id=order_id)
    if request.method == 'POST':
        form = EditPOForm(request.POST, instance=order)
        if form.is_valid():
            form.save()
            return redirect('editPO')
    else:
        form = EditPOForm(instance=order)
    items = LineItem.objects.filter(order=order).select_related('pattern')
    return render(request, 'dashboard/editPO.html',
                  {'form': form, 'order': order, 'items': items})

@login_required
def addCustomerView(request):
    if request.method == 'POST':
        form = AddCustomerForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    customer = form.save()
                return redirect(f"{reverse('addCustomer')}?saved={customer.customer_name}")
            except (IntegrityError, DataError) as exc:
                form.add_error(None, f"Could not save: {exc}")
    else:
        form = AddCustomerForm()
    return render(request, 'dashboard/addCustomer.html',
                  {'form': form, 'saved': request.GET.get('saved')})

@login_required
def addPatternView(request):
    if request.method == 'POST':
        form = AddPatternForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    pattern = form.save()
                return redirect(f"{reverse('addPattern')}?saved={pattern.sku}")
            except (IntegrityError, DataError) as exc:
                form.add_error(None, f"Could not save: {exc}")
    else:
        form = AddPatternForm()
    return render(request, 'dashboard/addPattern.html',
                  {'form': form, 'saved': request.GET.get('saved')})


@login_required
def importAuditView(request):
    """SKUs the sync could not place, for manual handling. Defaults to the open
    ones (the actual worklist); ?status=all shows resolved/ignored too."""
    if request.method == 'POST':
        issue = get_object_or_404(ImportAudit, pk=request.POST.get('issue_id'))
        action = request.POST.get('action')
        if action in (ImportAudit.OPEN, ImportAudit.RESOLVED, ImportAudit.IGNORED):
            issue.status = action
            issue.save(update_fields=['status'])
        # Preserve the filter the admin was looking at.
        qs = f"?status={request.POST.get('status')}" if request.POST.get('status') else ''
        return redirect(f"{reverse('importAudit')}{qs}")

    status = request.GET.get('status') or ImportAudit.OPEN
    rows = ImportAudit.objects.select_related('source')
    if status != 'all':
        rows = rows.filter(status=status)
    # Biggest skipped value first — that's what's most worth fixing.
    rows = rows.order_by('-total_value', '-occurrences')

    counts = {s: ImportAudit.objects.filter(status=s).count()
              for s, _ in ImportAudit.STATUS_CHOICES}
    return render(request, 'dashboard/importAudit.html',
                  {'rows': rows, 'status': status, 'counts': counts,
                   'total_open_value': sum(r.total_value for r in
                                           ImportAudit.objects.filter(status=ImportAudit.OPEN))})


# ── Ship & Bill ────────────────────────────────────────────────────────────
# Late fee: 1.5% of the order subtotal for every full 30 days the order is
# overdue, measured from the ship date. An order shipped and billed the same day
# incurs no fee; one shipped weeks before it's billed accrues a fee.
LATE_FEE_RATE_PER_PERIOD = Decimal('0.015')   # 1.5% per 30-day period
LATE_FEE_PERIOD_DAYS = 30

# Everyday Stitches company details.
COMPANY = {
    'contact': 'Jane Doe',
    'name': 'Everyday Stitches',
    'street': '123 Example Street',
    'city_line': 'Anytown, ST 00000',
    'phone': '555-555-0100',
    'email': 'info@example-store.com',
}
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'logo.png')


def _invoice_lines(order):
    """Per-line rows for an order's documents. Uses line_total where present,
    else estimates unit price from retail_price discounted by the customer's
    pct_of_retail (full retail when there's no linked customer). Returns
    (list of {qty, sku, name, unit, total}, subtotal)."""
    pct = (Decimal(order.customer.pct_of_retail)
           if order.customer and order.customer.pct_of_retail else Decimal(100))
    lines, subtotal = [], Decimal('0.00')
    for it in LineItem.objects.filter(order=order).select_related('pattern'):
        if it.line_total is not None:
            total = it.line_total
            unit = (total / it.quantity) if it.quantity else it.pattern.retail_price
        else:
            unit = it.pattern.retail_price * pct / Decimal(100)
            total = unit * Decimal(it.quantity)
        subtotal += total
        lines.append({
            'qty': it.quantity,
            'sku': it.pattern.sku,
            'name': it.pattern.pattern_name or '',
            'unit': Decimal(unit).quantize(Decimal('0.01')),
            'total': Decimal(total).quantize(Decimal('0.01')),
        })
    return lines, subtotal.quantize(Decimal('0.01'))


def _order_estimated_subtotal(order):
    """Subtotal for one order — same basis as the dashboard charts."""
    return _invoice_lines(order)[1]


def _fmt_date(d):
    """M/D/YYYY, matching the example documents (no leading zeros)."""
    return f'{d.month}/{d.day}/{d.year}' if d else '—'


def _doc_filename(prefix, order):
    """Filename for a document: '<prefix>_<customer abbr>_<customer PO#>.pdf'
    (e.g. 'INV_STH_PO-2026-965041.pdf'). Drops the abbr if the customer has
    none, and falls back to the order id when there's no PO number. Non
    filename-safe characters are replaced with '-'."""
    abbr = (order.customer.abbr if order.customer and order.customer.abbr else '').strip()
    po = (order.customers_po_number or '').strip() or str(order.order_id)
    parts = [prefix] + ([abbr] if abbr else []) + [po]
    name = re.sub(r'[^A-Za-z0-9._-]', '-', '_'.join(parts))
    return f'{name}.pdf'


def _bill_to_lines(order):
    """Bill-To block: customer name + best billing email."""
    c = order.customer
    if not c:
        return ['—']
    lines = [c.customer_name]
    email = c.invoice_email or c.contact_email
    if email:
        lines.append(email)
    return lines


def _ship_to_lines(order):
    """Ship-To block: the order's alternate ship-to address if present, else
    the customer's default address. Blank lines are dropped."""
    alt = order.different_ship
    if alt:
        name, street1, street2 = alt.name, alt.street1, alt.street2
        city, state, zc = alt.city, alt.state, alt.zip
        raw = [name, street1, street2]
    else:
        c = order.customer
        if not c:
            return ['—']
        name, addressee, street1 = c.customer_name, c.addressee, c.street_addr
        city, state, zc = c.city, c.state, c.zip_code
        raw = [name, addressee, street1]
    city_line = f"{(city or '').strip()}, {(state or '').strip()} {(zc or '').strip()}".strip()
    if city_line.strip(' ,'):
        raw.append(city_line)
    return [str(x).strip() for x in raw if x and str(x).strip()]


def _late_fee_for(order, as_of):
    """Late fee owed on `order` as of `as_of` (a date), measured from the ship
    date. Returns Decimal 0.00 when not overdue or when there's no ship date."""
    if not order.date_shipped:
        return Decimal('0.00')
    days_late = (as_of - order.date_shipped).days
    if days_late < LATE_FEE_PERIOD_DAYS:
        return Decimal('0.00')
    periods = days_late // LATE_FEE_PERIOD_DAYS
    pct = LATE_FEE_RATE_PER_PERIOD * periods
    return (_order_estimated_subtotal(order) * pct).quantize(Decimal('0.01'))


@login_required
def shipBillView(request):
    if request.method == 'POST':
        order = get_object_or_404(Order, order_id=request.POST.get('order_id'))
        # Creating a packing slip marks the order shipped (if it wasn't already),
        # but does not invoice it. Then open the packing-slip PDF.
        if request.POST.get('action') == 'packing':
            if order.date_shipped is None:
                order.date_shipped = datetime.date.today()
                order.save(update_fields=['date_shipped'])
            return redirect('packingSlip', order_id=order.order_id)
        # Create Invoice ships & bills the order, then opens the invoice PDF.
        today = datetime.date.today()
        if order.date_shipped is None:
            order.date_shipped = today
        order.date_invoiced = today
        order.late_fee = _late_fee_for(order, today)
        order.save(update_fields=['date_shipped', 'date_invoiced', 'late_fee'])
        return redirect('generatePDF', invoice_id=order.order_id)

    # Default view is orders that haven't shipped. The "unpaid" toggle switches
    # to every unpaid order (so shipped-but-unbilled orders can still be found
    # to invoice, since creating a packing slip drops them off the default list).
    show_unpaid = request.GET.get('unpaid') == '1'
    if show_unpaid:
        orders = Order.objects.filter(paid=False)
    else:
        orders = Order.objects.filter(date_shipped__isnull=True)
    orders = orders.select_related('customer').order_by('-order_id')
    po_query = request.GET.get('po', '').strip()
    cust_query = request.GET.get('cust', '').strip()
    if po_query:
        orders = orders.filter(customers_po_number__icontains=po_query)
    if cust_query:
        orders = orders.filter(customer__customer_name__icontains=cust_query)
    return render(request, 'dashboard/shipBill.html', {
        'orders': orders[:200],
        'po_query': po_query,
        'cust_query': cust_query,
        'show_unpaid': show_unpaid,
    })


def _draw_logo(p, x, top, w, h):
    """Draw the company logo, ignoring errors if the asset is missing."""
    try:
        p.drawImage(LOGO_PATH, x, 792 - (top + h), width=w, height=h,
                    preserveAspectRatio=True, mask='auto')
    except Exception:
        pass


@login_required
def generatePDF(request, invoice_id):
    """Invoice PDF, laid out to match the company's existing invoice template."""
    invoice = get_object_or_404(Order, order_id=invoice_id)
    lines, subtotal = _invoice_lines(invoice)
    late_fee = invoice.late_fee or Decimal('0.00')
    amount_due = subtotal + late_fee
    due_date = (invoice.date_invoiced + datetime.timedelta(days=30)
                if invoice.date_invoiced else None)

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{_doc_filename("INV", invoice)}"'
    p = canvas.Canvas(response, pagesize=letter)
    H = 792

    def yt(top):
        return H - top

    # Logo (same placement as the packing slip).
    _draw_logo(p, 27, 45, 201, 87)

    # Title + header block (right side).
    p.setFont('Helvetica-Bold', 40)
    p.drawRightString(578, yt(83), 'INVOICE')
    p.setFont('Helvetica', 11)
    header = [
        f'Invoice #: {invoice.order_id}',
        f'Invoice Date: {_fmt_date(invoice.date_invoiced)}',
        f'PO Number: {invoice.customers_po_number or "—"}',
        f'Due Date: {_fmt_date(due_date)}',
    ]
    top = 99
    for line in header:
        p.drawRightString(578, yt(top), line)
        top += 15

    # Amount Due box.
    p.setStrokeColorRGB(0.651, 0.651, 0.651)
    p.setLineWidth(1)
    p.rect(423, yt(203), 168, 48, stroke=1, fill=0)
    p.setFont('Helvetica-Bold', 12)
    p.drawRightString(415, yt(185), 'Amount Due')
    p.setFont('Helvetica-Bold', 22)
    p.drawRightString(582, yt(188), f'${amount_due:,.2f}')

    # Remit To.
    p.setFont('Helvetica-Bold', 14)
    p.drawString(33, yt(181), 'Remit To:')
    p.setFont('Helvetica', 11)
    top = 199
    for line in [COMPANY['contact'], COMPANY['name'], COMPANY['street'],
                 COMPANY['city_line'], COMPANY['phone'], COMPANY['email']]:
        p.drawString(40, yt(top), line)
        top += 14

    # Bill To.
    p.setFont('Helvetica-Bold', 14)
    p.drawString(33, yt(289), 'Bill To:')
    p.setFont('Helvetica', 11)
    top = 307
    for line in _bill_to_lines(invoice):
        p.drawString(40, yt(top), line)
        top += 14

    # Line-items table.
    p.setFont('Helvetica-Bold', 11)
    top = 359
    p.drawString(70, yt(top), 'Quantity')
    p.drawString(141, yt(top), 'SKU')
    p.drawString(219, yt(top), 'Pattern Name')
    p.drawRightString(470, yt(top), 'Unit Cost')
    p.drawRightString(578, yt(top), 'Total Cost')
    p.setStrokeColorRGB(0.6, 0.6, 0.6)
    p.setLineWidth(0.5)
    p.line(33, yt(top + 6), 591, yt(top + 6))

    p.setFont('Helvetica', 11)
    top += 22
    for ln in lines:
        p.drawString(75, yt(top), str(ln['qty']))
        p.drawString(141, yt(top), ln['sku'])
        p.drawString(219, yt(top), ln['name'][:34])
        p.drawRightString(470, yt(top), f"${ln['unit']:,.2f}")
        p.drawRightString(578, yt(top), f"${ln['total']:,.2f}")
        top += 18

    # Optional late-fee line + total.
    if late_fee:
        p.drawRightString(470, yt(top + 4), 'Late Fee')
        p.drawRightString(578, yt(top + 4), f'${late_fee:,.2f}')
        top += 18
    p.line(360, yt(top + 2), 591, yt(top + 2))
    p.setFont('Helvetica-Bold', 12)
    p.drawRightString(470, yt(top + 20), 'Amount Due')
    p.drawRightString(578, yt(top + 20), f'${amount_due:,.2f}')

    # Late-fee policy note + footer.
    p.setFont('Helvetica-Oblique', 9)
    p.drawString(33, yt(720),
                 'Late fees: If invoice is not paid by the due date, 1.5% will be '
                 'charged for every 30 days and a new invoice will be sent.')
    p.setFont('Helvetica', 9)
    p.drawString(33, yt(762), datetime.date.today().strftime('%A, %B %d, %Y'))
    p.drawRightString(578, yt(762), 'Page 1 of 1')

    p.showPage()
    p.save()
    return response


@login_required
def generatePackingSlip(request, order_id):
    """Packing slip PDF, laid out to match the company's existing template.
    Read-only: it does not modify the order."""
    order = get_object_or_404(Order, order_id=order_id)
    lines, _ = _invoice_lines(order)

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{_doc_filename("PS", order)}"'
    p = canvas.Canvas(response, pagesize=letter)
    H = 792

    def yt(top):
        return H - top

    # Logo + title.
    _draw_logo(p, 27, 45, 201, 87)
    p.setFont('Helvetica-Bold', 40)
    p.drawRightString(585, yt(83), 'PACKING SLIP')
    p.setFont('Helvetica', 11)
    p.drawRightString(585, yt(112), f'Date: {_fmt_date(order.date_shipped or datetime.date.today())}')

    # Return address line under the logo.
    p.setFont('Helvetica', 10)
    p.drawString(27, yt(152), f"{COMPANY['street']} | {COMPANY['city_line']}")

    # Ship To.
    p.setFont('Helvetica-Bold', 12)
    p.drawString(33, yt(178), 'Ship To:')
    p.setFont('Helvetica', 11)
    top = 196
    for line in _ship_to_lines(order):
        p.drawString(40, yt(top), line)
        top += 15

    # Order identifiers, with a blank line separating them from the address.
    top += 15
    p.setFont('Helvetica', 11)
    p.drawString(36, yt(top),
                 f'PO Number: {order.customers_po_number or "—"}      '
                 f'Date Ordered: {_fmt_date(order.date_ordered)}')

    # Line-items table (no pricing on a packing slip).
    top += 35
    p.setFont('Helvetica-Bold', 11)
    p.drawString(70, yt(top), 'Quantity')
    p.drawString(126, yt(top), 'SKU')
    p.drawString(249, yt(top), 'Pattern Name')
    p.setStrokeColorRGB(0.6, 0.6, 0.6)
    p.setLineWidth(0.5)
    p.line(33, yt(top + 6), 585, yt(top + 6))

    p.setFont('Helvetica', 11)
    top += 22
    for ln in lines:
        p.drawString(75, yt(top), str(ln['qty']))
        p.drawString(126, yt(top), ln['sku'])
        p.drawString(249, yt(top), ln['name'][:40])
        top += 18

    p.setFont('Helvetica-Oblique', 11)
    p.drawString(33, yt(top + 20), 'Thank you for your order.')

    p.showPage()
    p.save()
    return response



# ── Print jobs ─────────────────────────────────────────────────────────────
@login_required
def newPrintJobView(request):
    if request.method == 'POST':
        form = NewPrintJobForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    job = form.save()
                return redirect(f"{reverse('newPrintJob')}?saved={job.print_po_number}")
            except (IntegrityError, DataError) as exc:
                form.add_error(None, f"Could not save: {exc}")
    else:
        form = NewPrintJobForm()

    # REORDER: pre-fill the form from an existing job. The PO number and dates
    # are new — only the title and the items carry over.
    prefill_rows, source = [], None
    reorder_id = request.GET.get('reorder')
    if reorder_id and request.method != 'POST':
        source = PrintJob.objects.filter(pk=reorder_id).first()
        if source:
            form = NewPrintJobForm(initial={'title': source.title,
                                            'total_cost': source.total_cost})
            for li in source.printjoblineitem_set.all():
                prefill_rows.append({
                    'pattern_id': li.pattern_id,
                    'description': li.description or '',
                    'line_id': li.line_id,
                    'url': li.url or '',
                    'qty': li.qty,
                })
    if not prefill_rows:
        prefill_rows = [{'pattern_id': None, 'description': '',
                         'line_id': None, 'url': '', 'qty': ''}]

    # pattern -> {configuration: saved artwork URL}, so picking a pattern and a
    # configuration pre-fills the link the user last saved for that pairing.
    url_map = {}
    for pu in PatternUrl.objects.all():
        url_map.setdefault(str(pu.pattern_id), {})[str(pu.line_id)] = pu.url

    return render(request, 'dashboard/newPrintJob.html', {
        'form': form,
        'patterns': Pattern.objects.order_by('sku'),
        'print_items': PrintItem.objects.order_by('description'),
        'pattern_url_map': url_map,
        'next_po': PrintJob.next_po_number(),
        'saved': request.GET.get('saved'),
        'prefill_rows': prefill_rows,
        'reorder_source': source,
    })


@login_required
def printJobsView(request):
    """List print jobs. By default only those not yet received, soonest
    requested date first; the toggle shows everything, newest order first."""
    show_all = request.GET.get('all') == '1'
    statuses = list(PrintJobStatus.objects.order_by('sort_order'))
    first = statuses[0] if statuses else None
    final = statuses[-1] if statuses else None

    jobs = PrintJob.objects.select_related('status')
    if show_all:
        jobs = jobs.order_by('-date_submitted', '-print_job_id')
    else:
        if final:
            jobs = jobs.exclude(status=final)
        jobs = jobs.order_by('date_requested', 'print_job_id')

    # Attach each job's next status so the advance button can name it, rather
    # than making the template query per row.
    following = {s.status_id: (statuses[i + 1] if i + 1 < len(statuses) else None)
                 for i, s in enumerate(statuses)}
    # Same idea for the undo button: the status this job would step back to.
    preceding = {s.status_id: (statuses[i - 1] if i > 0 else None)
                 for i, s in enumerate(statuses)}
    jobs = list(jobs)
    for job in jobs:
        job.next_status_obj = following.get(job.status_id) if job.status_id else first
        job.next_is_final = bool(final and job.next_status_obj
                                 and job.next_status_obj.status_id == final.status_id)
        job.prev_status_obj = preceding.get(job.status_id) if job.status_id else None
        # Undoing from the final status also reverses the received inventory.
        job.undo_reverses_inventory = bool(final and job.status_id == final.status_id)

    return render(request, 'dashboard/printJobs.html', {
        'jobs': jobs,
        'show_all': show_all,
        'statuses': statuses,
        'final_status_id': final.status_id if final else None,
    })


@login_required
def printJobNextStatusView(request, print_job_id):
    """Advance a job to the next workflow status. Landing on the final status
    receives the job: each line with a pattern adds its qty to that pattern's
    inventory (one-off 'OTHER' items aren't tracked, so they're skipped)."""
    job = get_object_or_404(PrintJob, pk=print_job_id)
    if request.method == 'POST':
        nxt = (job.status.next_status() if job.status
               else PrintJobStatus.objects.order_by('sort_order').first())
        if nxt:
            with transaction.atomic():
                job.status = nxt
                job.save(update_fields=['status'])
                if nxt.is_final:
                    for li in job.printjoblineitem_set.filter(pattern__isnull=False):
                        (Pattern.objects.filter(pattern_id=li.pattern_id)
                         .update(inventory=F('inventory') + li.qty))
    return redirect(f"{reverse('printJobs')}{'?all=1' if request.GET.get('all') == '1' else ''}")


@login_required
def printJobPrevStatusView(request, print_job_id):
    """Undo the last workflow step — for a mis-click on the advance button.

    Stepping back OFF the final status also reverses the inventory that
    receiving added, so re-advancing later doesn't double-count. That is safe
    because advancing is the only thing that changes a job's status (the detail
    form edits title/date/cost/lines, never status), so a job on the final
    status always had its quantities added exactly once.
    """
    job = get_object_or_404(PrintJob, pk=print_job_id)
    if request.method == 'POST' and job.status:
        prev = job.status.prev_status()
        if prev:
            undoing_receipt = job.status.is_final
            with transaction.atomic():
                if undoing_receipt:
                    for li in job.printjoblineitem_set.filter(pattern__isnull=False):
                        (Pattern.objects.filter(pattern_id=li.pattern_id)
                         .update(inventory=F('inventory') - li.qty))
                job.status = prev
                job.save(update_fields=['status'])
    return redirect(f"{reverse('printJobs')}{'?all=1' if request.GET.get('all') == '1' else ''}")


@login_required
def printJobDetailView(request, print_job_id):
    """REVIEW: the PO's details, editable, with a button to create the PDF."""
    job = get_object_or_404(PrintJob.objects.select_related('status'), pk=print_job_id)

    if request.method == 'POST':
        form = EditPrintJobForm(request.POST, job=job)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save()
                return redirect(f"{reverse('printJobDetail', args=[job.print_job_id])}?saved=1")
            except (IntegrityError, DataError) as exc:
                form.add_error(None, f"Could not save: {exc}")
        # Redisplay what was submitted so edits aren't lost on a validation error.
        prefill_rows = form.line_rows()
    else:
        form = EditPrintJobForm(job=job, initial={
            'title': job.title,
            'date_requested': job.date_requested,
            'total_cost': job.total_cost,
        })
        prefill_rows = [{
            'pattern_id': i.pattern_id,
            'description': i.description or '',
            'line_id': i.line_id,
            'url': i.url or '',
            'qty': i.qty,
        } for i in job.printjoblineitem_set.order_by('print_job_line_id')]

    if not prefill_rows:
        prefill_rows = [{'pattern_id': None, 'description': '',
                         'line_id': None, 'url': '', 'qty': ''}]

    url_map = {}
    for pu in PatternUrl.objects.all():
        url_map.setdefault(str(pu.pattern_id), {})[str(pu.line_id)] = pu.url

    used = [r['line_id'] for r in prefill_rows if r.get('line_id')]
    return render(request, 'dashboard/printJobDetail.html', {
        'job': job,
        'form': form,
        'prefill_rows': prefill_rows,
        'patterns': Pattern.objects.order_by('sku'),
        'print_items': PrintItem.objects.order_by('description'),
        'pattern_url_map': url_map,
        'configs': PrintItem.objects.filter(line_id__in=used).distinct(),
        'saved': request.GET.get('saved'),
    })


def _config_lines(raw_html):
    """Turn a print configuration's stored HTML into plain text lines. The first
    line is the configuration's name; the rest are its spec lines."""
    if not raw_html:
        return []
    text = re.sub(r'(?i)<br\s*/?>', '\n', raw_html)
    text = re.sub(r'(?i)</(p|div|li)>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    return [ln.strip() for ln in html_lib.unescape(text).splitlines() if ln.strip()]


@login_required
def printJobPDF(request, print_job_id):
    """The printer-facing PO, laid out to match the company's existing print
    job documents."""
    job = get_object_or_404(PrintJob, pk=print_job_id)
    items = list(job.printjoblineitem_set
                 .select_related('pattern', 'line').order_by('print_job_line_id'))

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{job.pdf_filename}"'
    p = canvas.Canvas(response, pagesize=letter)

    H, LEFT, RIGHT, BOTTOM = 792, 45, 567, 720
    BLUE = (0.098, 0.463, 0.824)          # #1976d2, the colour used in the specs
    state = {'top': 0, 'page': 1}

    def yt(top):
        return H - top

    def draw_footer():
        p.setFont('Helvetica', 9)
        p.setFillColorRGB(0.45, 0.45, 0.45)
        p.drawCentredString((LEFT + RIGHT) / 2, yt(755),
                            f'Office Operations                Page {state["page"]}')
        p.setFillColorRGB(0, 0, 0)

    def new_page():
        draw_footer()
        p.showPage()
        state['page'] += 1
        state['top'] = 60

    def ensure(space):
        if state['top'] + space > BOTTOM:
            new_page()

    # ── Company logo, top-right (aspect ~2.29:1).
    _draw_logo(p, RIGHT - 150, 30, 150, 65)

    # ── Heading: '<PO number> - <title> - <month>' plus a generated timestamp
    heading = job.pdf_title
    p.setFont('Helvetica-Bold', 15)
    p.drawString(LEFT, yt(58), heading)
    p.setFont('Helvetica', 9.5)
    p.setFillColorRGB(0.42, 0.42, 0.42)
    p.drawString(LEFT, yt(76), datetime.datetime.now().strftime('%A, %B %d, %Y %I:%M %p'))
    p.setFillColorRGB(0, 0, 0)

    # ── PO / Requested Date summary box
    box_top, box_h = 104, 46
    p.setStrokeColorRGB(0.72, 0.72, 0.72)
    p.setLineWidth(0.75)
    p.rect(LEFT + 20, yt(box_top + box_h), 260, box_h, stroke=1, fill=0)
    p.line(LEFT + 20, yt(box_top + 23), LEFT + 280, yt(box_top + 23))
    p.line(LEFT + 150, yt(box_top + box_h), LEFT + 150, yt(box_top))
    p.setFont('Helvetica-Bold', 10)
    p.drawCentredString(LEFT + 85, yt(box_top + 15), 'PO')
    p.drawCentredString(LEFT + 215, yt(box_top + 15), 'Requested Date')
    p.setFont('Helvetica', 10)
    p.drawCentredString(LEFT + 85, yt(box_top + 38), job.print_po_number)
    p.drawCentredString(LEFT + 215, yt(box_top + 38),
                        job.date_requested.strftime('%d %b %Y') if job.date_requested else '—')

    # ── SKUs and Order Quantities
    state['top'] = 178
    p.setFont('Helvetica-Bold', 12)
    p.drawString(LEFT, yt(state['top']), 'SKUs and Order Quantities')
    state['top'] += 22

    COL_SKU, COL_DESC, COL_CFG, COL_LINK, COL_QTY = LEFT, LEFT + 92, LEFT + 235, LEFT + 350, RIGHT

    def item_header():
        p.setFont('Helvetica-Bold', 9.5)
        p.drawString(COL_SKU, yt(state['top']), 'SKU')
        p.drawString(COL_DESC, yt(state['top']), 'Description')
        p.drawString(COL_CFG, yt(state['top']), 'Configuration')
        p.drawString(COL_LINK, yt(state['top']), 'Link')
        p.drawRightString(COL_QTY, yt(state['top']), 'Qty')
        p.setStrokeColorRGB(0.7, 0.7, 0.7)
        p.setLineWidth(0.5)
        p.line(LEFT, yt(state['top'] + 5), RIGHT, yt(state['top'] + 5))
        state['top'] += 17

    item_header()
    p.setFont('Helvetica', 9.5)
    for it in items:
        ensure(20)
        if state['top'] < 70:          # fresh page — repeat the column headers
            item_header()
            p.setFont('Helvetica', 9.5)
        y = yt(state['top'])
        p.drawString(COL_SKU, y, (it.pattern.sku if it.pattern else '—')[:14])
        desc = (it.pattern.pattern_name if it.pattern else it.description) or '—'
        p.drawString(COL_DESC, y, desc[:26])
        p.drawString(COL_CFG, y, ((it.line.config or it.line.description) if it.line else '—')[:20])
        if it.url:
            p.setFillColorRGB(*BLUE)
            shown = it.url if len(it.url) <= 34 else it.url[:33] + '…'
            p.drawString(COL_LINK, y, shown)
            p.linkURL(it.url, (COL_LINK, y - 2, COL_LINK + 150, y + 9), relative=0)
            p.setFillColorRGB(0, 0, 0)
        else:
            p.drawString(COL_LINK, y, '—')
        p.drawRightString(COL_QTY, y, str(it.qty))
        state['top'] += 17

    # ── Configuration specs, one block per distinct configuration used
    seen, blocks = set(), []
    for it in items:
        if it.line and it.line_id not in seen:
            seen.add(it.line_id)
            blocks.append(it.line)

    if blocks:
        state['top'] += 18
        ensure(40)
        p.setFont('Helvetica-Bold', 12)
        p.drawString(LEFT, yt(state['top']), 'Configuration')
        state['top'] += 22

    for cfg in blocks:
        lines = _config_lines(cfg.html) or [cfg.config or cfg.description or '']
        ensure(20 + 14 * len(lines))
        p.setFillColorRGB(*BLUE)
        p.setFont('Helvetica-Bold', 11.5)
        p.drawString(LEFT, yt(state['top']), lines[0])
        p.setFillColorRGB(0, 0, 0)
        state['top'] += 16
        p.setFont('Helvetica', 10)
        for spec in lines[1:]:
            ensure(14)
            p.drawString(LEFT + 26, yt(state['top']), spec)
            state['top'] += 14
        state['top'] += 10

    draw_footer()
    p.showPage()
    p.save()
    return response
