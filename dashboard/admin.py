from django.apps import apps
from django.contrib import admin
from django.db import models


# Turn the admin into a "database viewer": every model's changelist shows all of
# its fields as columns. list_display is generated from the model's concrete
# fields, so it stays in sync automatically as the schema changes — no need to
# hand-maintain a column list per model.

def _build_admin(model):
    concrete = model._meta.fields  # concrete fields only (excludes M2M/reverse)

    # For each foreign key, show BOTH the raw id column (attname, e.g.
    # "customer_id" -> the integer stored in Postgres) and the friendly related
    # object (name, e.g. "customer" -> the Customer's __str__). Non-relations
    # show their single field name. Mirrors a `SELECT *` plus a joined label.
    fields = []
    for f in concrete:
        if f.is_relation:
            fields.append(f.attname)   # raw FK id, e.g. "customer_id"
            fields.append(f.name)      # related object, e.g. "customer"
        else:
            fields.append(f.name)
    relations = [f.name for f in concrete if f.is_relation]

    # Free-text search across char/text columns; cheap wins for a viewer.
    text_fields = [
        f.name for f in concrete
        if isinstance(f, (models.CharField, models.TextField)) and not f.is_relation
    ]
    # Boolean columns make good sidebar filters.
    bool_fields = [f.name for f in concrete if isinstance(f, models.BooleanField)]

    return type(f'{model.__name__}Admin', (admin.ModelAdmin,), {
        'list_display': fields,
        'list_select_related': relations,   # avoid N+1 on FK columns
        'search_fields': text_fields,
        'list_filter': bool_fields,
        'list_per_page': 50,
    })


for _model in apps.get_app_config('dashboard').get_models():
    try:
        admin.site.register(_model, _build_admin(_model))
    except admin.sites.AlreadyRegistered:
        pass
