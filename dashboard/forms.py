from django import forms
from .models import (Customer, Pattern, Order, LineItem, SalesChannel, AlternateShip,
                     Expense, OrderNote, PrintItem, PatternUrl, PrintJob,
                     PrintJobStatus, PrintJobLineItem)
import datetime


class AddExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['date', 'amount', 'schedule_c', 'vendor', 'memo']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }
        labels = {'schedule_c': 'Category (Schedule C line)'}


SHIP_FIELDS = ['ship_name', 'ship_street1', 'ship_street2',
               'ship_city', 'ship_state', 'ship_zip', 'ship_billing']


class AltShipMixin(forms.Form):
    """Optional alternate ship-to address fields, shared by the PO forms.

    If any field is filled in, an AlternateShip row is created/updated and
    linked to the order. If all are blank, the order ships to the customer's
    default address (Order.different_ship stays NULL).
    """
    ship_name    = forms.CharField(max_length=100, required=False, label="Ship-to name")
    ship_street1 = forms.CharField(max_length=100, required=False, label="Street 1")
    ship_street2 = forms.CharField(max_length=100, required=False, label="Street 2")
    ship_city    = forms.CharField(max_length=35,  required=False, label="City")
    ship_state   = forms.CharField(max_length=2,   required=False, label="State")
    ship_zip     = forms.CharField(max_length=9,   required=False, label="ZIP")
    ship_billing = forms.CharField(max_length=30,  required=False, label="Billing")

    def _has_alt_ship(self):
        return any((self.cleaned_data.get(f) or '').strip() for f in SHIP_FIELDS)

    def _apply_alt_ship(self, alt):
        cd = self.cleaned_data
        alt.name    = (cd.get('ship_name') or '').strip() or None
        alt.street1 = (cd.get('ship_street1') or '').strip() or None
        alt.street2 = (cd.get('ship_street2') or '').strip() or None
        alt.city    = (cd.get('ship_city') or '').strip() or None
        alt.state   = (cd.get('ship_state') or '').strip() or None
        alt.zip     = (cd.get('ship_zip') or '').strip() or None
        alt.billing = (cd.get('ship_billing') or '').strip() or None
        alt.save()
        return alt

    def build_alt_ship(self):
        """Create + return a saved AlternateShip if any field is filled, else None."""
        if self._has_alt_ship():
            return self._apply_alt_ship(AlternateShip())
        return None


class EditPOForm(AltShipMixin, forms.ModelForm):
    field_order = [
        'customers_po_number', 'customer', 'source',
        'date_ordered', 'date_shipped', 'date_invoiced', 'payment_date',
        'paid', 'late_fee', 'shipping_paid_by_customer',
    ] + SHIP_FIELDS

    class Meta:
        model = Order
        fields = [
            'customers_po_number', 'customer', 'source',
            'date_ordered', 'date_shipped', 'date_invoiced', 'payment_date',
            'paid', 'late_fee', 'shipping_paid_by_customer',
        ]
        widgets = {
            'date_ordered':  forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'date_shipped':  forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'date_invoiced': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'payment_date':  forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pre-fill the ship-to fields from the order's existing alternate address.
        alt = self.instance.different_ship if self.instance and self.instance.pk else None
        if alt:
            self.fields['ship_name'].initial    = alt.name
            self.fields['ship_street1'].initial  = alt.street1
            self.fields['ship_street2'].initial  = alt.street2
            self.fields['ship_city'].initial     = alt.city
            self.fields['ship_state'].initial    = (alt.state or '').strip()
            self.fields['ship_zip'].initial      = (alt.zip or '').strip()
            self.fields['ship_billing'].initial  = alt.billing

    def save(self, commit=True):
        order = super().save(commit=False)
        # When an order is marked paid but no payment date was entered, stamp
        # today's date (mirrors the Mark P.O. Paid batch action).
        if order.paid and not order.payment_date:
            order.payment_date = datetime.date.today()
        if self._has_alt_ship():
            # update the existing alternate address, or create a new one
            alt = order.different_ship or AlternateShip()
            self._apply_alt_ship(alt)
            order.different_ship = alt
        else:
            order.different_ship = None
        if commit:
            order.save()
        return order


class AddPatternForm(forms.ModelForm):
    class Meta:
        model = Pattern
        fields = [
            'type', 'sku', 'pattern_name', 'retail_price', 'inventory', 'year_introduced',
            'ean', 'everyday_applique', 'applique', 'precuts', 'current', 'curve',
            'landmark',
        ]
        labels = {'ean': 'EAN', 'type': 'Type'}
        help_texts = {
            'sku': "Patterns use ES-###-LLL (e.g. ES-407-IRO); resale/bundle SKUs "
                   "may differ. Auto-normalized on save.",
            'type': "Pattern (incl. notions & templates), Resale item (e.g. rulers), "
                    "or Bundle (define its parts in the bundles table).",
        }
        widgets = {
            'ean': forms.TextInput(attrs={'style': 'text-transform:uppercase'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Sensible defaults for a brand-new pattern: this year, and marked current.
        self.fields['year_introduced'].initial = datetime.date.today().year
        self.fields['current'].initial = True

    def clean_ean(self):
        # Store the EAN uppercased (matches the all-caps display).
        return (self.cleaned_data.get('ean') or '').strip().upper() or None


class AddCustomerForm(forms.ModelForm):
    type = forms.ChoiceField(
        choices=[('', '---------'), ('D', 'Distributor'), ('W', 'Wholesale'),
                 ('R', 'Retail'), ('S', 'Sample/Show')],
        required=False, label="Type",
    )

    class Meta:
        model = Customer
        fields = [
            'customer_name', 'contact_name', 'contact_email', 'invoice_email',
            'type', 'pct_of_retail', 'addressee', 'street_addr',
            'city', 'state', 'zip_code', 'abbr',
        ]


class NewPOForm(AltShipMixin):
    date_ordered = forms.DateField(
        initial=datetime.date.today,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    customers_po_number = forms.CharField(max_length=20, label="PO number")
    customer = forms.ModelChoiceField(queryset=Customer.objects.all())
    # Realistically only two channels are used: Distributor ('D') and Other
    # ('X'). Default to Distributor. The customer list is narrowed to match the
    # chosen channel client-side (see newPO.html).
    source = forms.ModelChoiceField(
        queryset=SalesChannel.objects.filter(id__in=['D', 'X']).order_by('id'),
        label="Type", initial='D',
    )

    product = forms.ModelChoiceField(
        queryset=Pattern.objects.all(),
        empty_label="Select a product",
    )
    quantity = forms.IntegerField(min_value=1, initial=1)
    note = forms.CharField(
        required=False, label="Note (optional)",
        widget=forms.Textarea(attrs={'rows': 2, 'cols': 40,
                                     'placeholder': 'Optional note for this order'}),
    )

    def save(self):
        customer = self.cleaned_data['customer']
        alt_ship = self.build_alt_ship()
        order = Order.objects.create(
            customers_po_number=self.cleaned_data['customers_po_number'],
            date_ordered=self.cleaned_data['date_ordered'],
            customer=customer,
            source=self.cleaned_data['source'],
            different_ship=alt_ship,
            paid=False,
            # Default to fully shipped; backorders are reconciled manually later.
            shipping_paid_by_customer=True,
        )

        products = self.data.getlist('product')
        quantities = self.data.getlist('quantity')
        factor = int(customer.pct_of_retail or 0) / 100

        for pattern_id, quantity in zip(products, quantities):
            if pattern_id and quantity:
                pattern = Pattern.objects.get(pattern_id=pattern_id)
                qty = int(quantity)
                line_total = qty * float(pattern.retail_price) * factor
                LineItem.objects.create(
                    order=order,
                    pattern=pattern,
                    quantity=qty,
                    shipped=True,
                    physical=True,
                    line_total=line_total,
                )

        # Attach an optional note to the order.
        note = (self.cleaned_data.get('note') or '').strip()
        if note:
            OrderNote.objects.create(order=order, note=note)

        return order


class NewPrintJobForm(forms.Form):
    """Create a print PO. The PO number is generated on save; line-item rows
    are dynamic, so they're parsed out of the raw POST data (same approach the
    New P.O. form uses)."""
    title = forms.CharField(max_length=255, label="Title")
    date_requested = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        label="Requested date",
    )
    total_cost = forms.DecimalField(max_digits=10, decimal_places=2,
                                    required=False, label="Total cost")

    def clean_date_requested(self):
        requested = self.cleaned_data['date_requested']
        if requested <= datetime.date.today():
            raise forms.ValidationError("Requested date must be after today.")
        return requested

    def clean(self):
        cleaned = super().clean()
        if not self.line_rows():
            raise forms.ValidationError(
                "Add at least one item with a pattern (or name), a configuration, "
                "and a quantity greater than zero.")
        return cleaned

    def line_rows(self):
        """Parse the dynamic line-item rows. Rows missing a configuration or a
        valid quantity are skipped; a row needs either a pattern or a name."""
        data = self.data
        patterns = data.getlist('pj_pattern')
        descs = data.getlist('pj_desc')
        configs = data.getlist('pj_config')
        urls = data.getlist('pj_url')
        qtys = data.getlist('pj_qty')

        rows = []
        for i in range(len(qtys)):
            def at(seq):
                return (seq[i] if i < len(seq) else '') or ''

            config = at(configs).strip()
            qty_raw = at(qtys).strip()
            if not config or not qty_raw:
                continue
            try:
                qty = int(qty_raw)
            except ValueError:
                continue
            pattern = at(patterns).strip()
            desc = at(descs).strip()
            if qty <= 0 or (not pattern and not desc):
                continue
            rows.append({
                'pattern_id': int(pattern) if pattern else None,
                # description only applies to one-off ("OTHER") items
                'description': None if pattern else desc,
                'line_id': int(config),
                'url': at(urls).strip() or None,
                'qty': qty,
            })
        return rows

    def write_lines(self, job):
        """Replace the job's line items with the submitted rows, and remember
        (in place of the previous one) each pattern+configuration's artwork link."""
        job.printjoblineitem_set.all().delete()
        for row in self.line_rows():
            PrintJobLineItem.objects.create(
                print_job=job,
                pattern_id=row['pattern_id'],
                line_id=row['line_id'],
                description=row['description'],
                url=row['url'],
                qty=row['qty'],
            )
            if row['pattern_id'] and row['url']:
                PatternUrl.objects.update_or_create(
                    pattern_id=row['pattern_id'], line_id=row['line_id'],
                    defaults={'url': row['url']},
                )

    def save(self):
        cd = self.cleaned_data
        job = PrintJob.objects.create(
            print_po_number=PrintJob.next_po_number(),
            title=cd['title'],
            date_submitted=datetime.date.today(),
            date_requested=cd['date_requested'],
            total_cost=cd.get('total_cost'),
            status=PrintJobStatus.objects.order_by('sort_order').first(),
        )
        self.write_lines(job)
        return job


class EditPrintJobForm(NewPrintJobForm):
    """Edit an existing print job from the review screen. Same fields and rows
    as creating one, but it updates the job in place."""

    def __init__(self, *args, job=None, **kwargs):
        self.job = job
        super().__init__(*args, **kwargs)

    def clean_date_requested(self):
        # A job already in flight can legitimately have a requested date in the
        # past, so editing only enforces the database rule (after the order
        # date) rather than "after today" as creation does.
        requested = self.cleaned_data['date_requested']
        ordered = self.job.date_submitted if self.job else None
        if ordered and requested <= ordered:
            raise forms.ValidationError(
                f"Requested date must be after the order date ({ordered}).")
        return requested

    def save(self):
        cd = self.cleaned_data
        job = self.job
        job.title = cd['title']
        job.date_requested = cd['date_requested']
        job.total_cost = cd.get('total_cost')
        job.save(update_fields=['title', 'date_requested', 'total_cost'])
        self.write_lines(job)
        return job
