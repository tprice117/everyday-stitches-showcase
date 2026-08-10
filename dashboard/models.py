import datetime
import re

from django.db import models
from django.db.models import Max

class SalesChannel(models.Model):
    id = models.CharField(primary_key=True, max_length=1)
    name = models.CharField(max_length=50)

    class Meta:
        managed = False
        db_table = 'sales_channels'

    def __str__(self):
        return self.name


class Customer(models.Model):
    customer_id = models.AutoField(primary_key=True)
    customer_name = models.CharField(max_length=40)
    contact_name = models.CharField(max_length=50, blank=True, null=True)
    contact_email = models.CharField(max_length=50)
    invoice_email = models.CharField(max_length=255, blank=True, null=True)
    type = models.CharField(max_length=1, blank=True, null=True)
    pct_of_retail = models.SmallIntegerField(default=0)
    addressee = models.CharField(max_length=255, blank=True, null=True)
    street_addr = models.CharField(max_length=50, blank=True, null=True)
    city = models.CharField(max_length=35, blank=True, null=True)
    state = models.CharField(max_length=2, blank=True, null=True)
    zip_code = models.CharField(max_length=10, blank=True, null=True)
    abbr = models.CharField(max_length=3, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'customers'

    def __str__(self):
        return self.customer_name


class Pattern(models.Model):
    PATTERN, RESALE, BUNDLE = 'P', 'R', 'B'
    TYPE_CHOICES = [(PATTERN, 'Pattern'), (RESALE, 'Resale item'), (BUNDLE, 'Bundle')]

    pattern_id = models.AutoField(primary_key=True)
    pattern_name = models.CharField(max_length=255, blank=True, null=True)
    # Catalog type: pattern (incl. notions/templates), resale item (rulers), or
    # bundle (routed to the bundles table). See migration 004_add_product_type.
    type = models.CharField(max_length=1, choices=TYPE_CHOICES, default=PATTERN)
    year_introduced = models.SmallIntegerField(blank=True, null=True)
    sku = models.CharField(unique=True, max_length=20)
    inventory = models.IntegerField(default=0)
    everyday_applique = models.BooleanField(default=False)
    applique = models.BooleanField(default=False)
    precuts = models.BooleanField(default=False)
    retail_price = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    ean = models.CharField(max_length=13, blank=True, null=True)
    current = models.BooleanField(default=False)
    curve = models.BooleanField(default=False)
    # Marks a pattern as part of a specific landmark design line (e.g. KC Landmark).
    landmark = models.BooleanField(default=False)

    class Meta:
        managed = False
        # Table renamed patterns -> products (migration 005); the model class
        # stays `Pattern` and the PK column stays `pattern_id` to avoid a
        # 150-reference cascade for no functional gain.
        db_table = 'products'

    def __str__(self):
        return f'{self.sku} — {self.pattern_name}'


class SalesTax(models.Model):
    sales_tax_id = models.AutoField(primary_key=True)
    region = models.CharField(unique=True, max_length=20)
    rate = models.DecimalField(max_digits=6, decimal_places=4)
    effective_from = models.DateField()
    effective_to = models.DateField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'sales_tax'

    def __str__(self):
        return f'{self.region} @ {self.rate}'


class ScheduleC(models.Model):
    schedule_c_id = models.AutoField(primary_key=True)
    line_number = models.CharField(max_length=10)
    description = models.TextField()

    class Meta:
        managed = False
        db_table = 'schedule_c'

    def __str__(self):
        return f'{self.line_number} — {self.description}'


class AlternateShip(models.Model):
    ship_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, blank=True, null=True)
    street1 = models.CharField(max_length=100, blank=True, null=True)
    street2 = models.CharField(max_length=100, blank=True, null=True)
    city = models.CharField(max_length=35, blank=True, null=True)
    state = models.CharField(max_length=2, blank=True, null=True)
    zip = models.CharField(max_length=9, blank=True, null=True)
    billing = models.CharField(max_length=30, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'alternate_ship'

    def __str__(self):
        return self.name or f'Ship #{self.ship_id}'


class Order(models.Model):
    order_id = models.AutoField(primary_key=True)
    customers_po_number = models.CharField(max_length=20, blank=True, null=True)
    customer = models.ForeignKey(Customer, models.DO_NOTHING, blank=True, null=True)
    source = models.ForeignKey(SalesChannel, models.DO_NOTHING, db_column='source', blank=True, null=True)
    sales_tax = models.ForeignKey(SalesTax, models.DO_NOTHING, blank=True, null=True)
    different_ship = models.ForeignKey(AlternateShip, models.DO_NOTHING, db_column='different_ship_id', blank=True, null=True)
    date_ordered = models.DateField(blank=True, null=True)
    date_shipped = models.DateField(blank=True, null=True)
    paid = models.BooleanField(default=False)
    date_invoiced = models.DateField(blank=True, null=True)
    payment_date = models.DateField(blank=True, null=True)
    late_fee = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True)
    shipping_paid_by_customer = models.BooleanField(blank=True, null=True, default=True)
    # External system id (WooCommerce/PayPal/Etsy) for sync imports.
    external_id = models.CharField(max_length=64, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'orders'

    def __str__(self):
        return f'PO {self.customers_po_number or self.order_id}'


class LineItem(models.Model):
    line_item_id = models.AutoField(primary_key=True)
    order = models.ForeignKey(Order, models.DO_NOTHING)
    pattern = models.ForeignKey(Pattern, models.DO_NOTHING)
    quantity = models.SmallIntegerField(default=0)
    shipped = models.BooleanField(default=True)
    line_total = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    physical = models.BooleanField(default=True)
    # Raw SKU as submitted by the sales channel (e.g. 'ES-548-CHF(PP)'), for
    # traceability. NULL for manually entered line items.
    source_sku = models.CharField(max_length=64, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'line_items'

    def __str__(self):
        return f'{self.quantity} × {self.pattern.sku} on order {self.order_id}'


class ImportAudit(models.Model):
    """SKUs the sales-sync import could not place (matched neither a bundle nor a
    pattern), so an admin can handle them. Written by the sync pipeline; this app
    reads it and updates `status`."""
    OPEN, RESOLVED, IGNORED = 'open', 'resolved', 'ignored'
    STATUS_CHOICES = [(OPEN, 'Open'), (RESOLVED, 'Resolved'), (IGNORED, 'Ignored')]

    issue_id = models.AutoField(primary_key=True)
    source = models.ForeignKey(SalesChannel, models.DO_NOTHING, db_column='source')
    normalized_sku = models.CharField(max_length=64)
    source_sku = models.CharField(max_length=64, blank=True, null=True)
    description = models.CharField(max_length=255, blank=True, null=True)
    occurrences = models.IntegerField(default=0)
    total_quantity = models.IntegerField(default=0)
    total_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sample_order = models.CharField(max_length=64, blank=True, null=True)
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=OPEN)
    note = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'import_audit'
        unique_together = (('source', 'normalized_sku'),)

    def __str__(self):
        return f'{self.normalized_sku} ({self.status})'


class Bundle(models.Model):
    # Lookup table (managed by the sales-sync pipeline) mapping a bundle SKU to
    # its component patterns. The real primary key is composite
    # (bundle_sku, item_sku, source); Django 5.0 has no composite-PK support, so
    # bundle_sku is declared the PK just to satisfy the ORM and unique_together
    # records the true key. Read/reference use is safe; don't rely on pk-based
    # single-row lookups since bundle_sku repeats across a bundle's components.
    bundle_sku = models.CharField(primary_key=True, max_length=20)
    item = models.ForeignKey(Pattern, models.DO_NOTHING, db_column='item_sku', to_field='sku')
    alt_price = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.CharField(max_length=255, blank=True, null=True)
    source = models.ForeignKey(SalesChannel, models.DO_NOTHING, db_column='source')
    physical = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = 'bundles'
        unique_together = (('bundle_sku', 'item', 'source'),)

    def __str__(self):
        return f'{self.bundle_sku} → {self.item_id} (${self.alt_price})'


class OrderNote(models.Model):
    note_id = models.AutoField(primary_key=True)
    order = models.ForeignKey(Order, models.DO_NOTHING)
    note = models.TextField()

    class Meta:
        managed = False
        db_table = 'order_notes'

    def __str__(self):
        return f'Note on order {self.order_id}'


class Expense(models.Model):
    expense_id = models.AutoField(primary_key=True)
    schedule_c = models.ForeignKey(ScheduleC, models.DO_NOTHING)
    date = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    vendor = models.CharField(max_length=100, blank=True, null=True)
    memo = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'expenses'

    def __str__(self):
        return f'{self.date} — {self.vendor or "expense"} ${self.amount}'


# ── Print jobs ────────────────────────────────────────────────────────────
class PrintItem(models.Model):
    """A print configuration — e.g. 'Large - Cover' / 'TriFold Pattern'.
    `html` is the spec block rendered in a print PO's Configuration section."""
    line_id = models.AutoField(primary_key=True)
    description = models.CharField(max_length=100)
    short_code = models.CharField(max_length=20, blank=True, null=True)
    config = models.CharField(max_length=50, blank=True, null=True)
    html = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'print_items'

    def __str__(self):
        return self.config or self.description


class PatternUrl(models.Model):
    """The artwork link for one pattern in one print configuration. A pattern
    has a separate URL per configuration (Cover vs. Insert, etc.)."""
    pattern_url_id = models.AutoField(primary_key=True)
    pattern = models.ForeignKey(Pattern, models.DO_NOTHING)
    line = models.ForeignKey(PrintItem, models.DO_NOTHING)
    url = models.CharField(max_length=500)

    class Meta:
        managed = False
        db_table = 'pattern_urls'
        unique_together = (('pattern', 'line'),)

    def __str__(self):
        return f'{self.pattern.sku} — {self.line}'


class PrintJobStatus(models.Model):
    """Ordered workflow states. `sort_order` drives the NEXT button; the row
    with the highest sort_order is terminal (received → add qty to inventory).
    `alert` shows a dashboard banner while a job sits in this state."""
    status_id = models.AutoField(primary_key=True)
    sort_order = models.SmallIntegerField(unique=True)
    description = models.CharField(max_length=50)
    alert = models.BooleanField(default=False)

    class Meta:
        managed = False
        db_table = 'print_job_status'
        ordering = ['sort_order']

    def __str__(self):
        return self.description

    def next_status(self):
        """The next state in the workflow, or None if this is the last one."""
        return (PrintJobStatus.objects
                .filter(sort_order__gt=self.sort_order)
                .order_by('sort_order').first())

    def prev_status(self):
        """The previous state in the workflow, or None if this is the first one.
        Backs the undo button on the print jobs list."""
        return (PrintJobStatus.objects
                .filter(sort_order__lt=self.sort_order)
                .order_by('-sort_order').first())

    @property
    def is_final(self):
        return self.next_status() is None


class PrintJob(models.Model):
    print_job_id = models.AutoField(primary_key=True)
    print_po_number = models.CharField(max_length=15, unique=True)
    title = models.CharField(max_length=255)
    date_submitted = models.DateField(default=datetime.date.today)
    date_requested = models.DateField(blank=True, null=True)
    total_cost = models.DecimalField(max_digits=10, decimal_places=2,
                                     blank=True, null=True)
    status = models.ForeignKey(PrintJobStatus, models.DO_NOTHING,
                               blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'print_jobs'

    def __str__(self):
        return f'{self.print_po_number} — {self.title}'

    @classmethod
    def next_po_number(cls, year=None):
        """Next PO number, 'ES-YYYY-####'. The sequence is per-year and restarts
        at 0001 when the year changes, so it's derived from the existing PO
        numbers for that year rather than from the table's primary key."""
        year = year or datetime.date.today().year
        prefix = f'ES-{year}-'
        used = []
        for po in (cls.objects.filter(print_po_number__startswith=prefix)
                   .values_list('print_po_number', flat=True)):
            suffix = po[len(prefix):]
            if suffix.isdigit():
                used.append(int(suffix))
        return f'{prefix}{(max(used) + 1) if used else 1:04d}'

    @property
    def pdf_title(self):
        """Human-readable document title: '<PO number> - <title> - <month>'.
        Used for the heading shown inside the PDF (keeps spaces for readability)."""
        month = self.date_requested.strftime('%B') if self.date_requested else ''
        stem = f'{self.print_po_number} - {self.title}'
        if month:
            stem = f'{stem} - {month}'
        return stem

    @property
    def pdf_filename(self):
        """Download filename derived from pdf_title with the spaces removed, e.g.
        'ES-2026-0003-789test-July.pdf'. Whitespace collapses to a single dash and
        any filename-unsafe characters are replaced, so the name is URL/safe."""
        safe = re.sub(r'\s+', '-', self.pdf_title.strip())
        safe = re.sub(r'[^A-Za-z0-9._-]', '-', safe)
        safe = re.sub(r'-{2,}', '-', safe).strip('-')
        return f'{safe}.pdf'


class PrintJobLineItem(models.Model):
    """One SKU/configuration on a print PO. `pattern` is NULL for a one-off
    ('OTHER') item — then `description` names it and `url` holds its link."""
    print_job_line_id = models.AutoField(primary_key=True)
    print_job = models.ForeignKey(PrintJob, models.DO_NOTHING)
    pattern = models.ForeignKey(Pattern, models.DO_NOTHING, blank=True, null=True)
    line = models.ForeignKey(PrintItem, models.DO_NOTHING, blank=True, null=True)
    description = models.CharField(max_length=255, blank=True, null=True)
    url = models.CharField(max_length=500, blank=True, null=True)
    qty = models.IntegerField()

    class Meta:
        managed = False
        db_table = 'print_job_line_items'

    def __str__(self):
        name = self.pattern.sku if self.pattern else (self.description or 'item')
        return f'{self.qty} × {name}'
