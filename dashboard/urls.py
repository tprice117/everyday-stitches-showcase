from django.urls import path
from .views import (
    indexPageView, newPOView, successView, generatePDF,
    addExpenseView, modifyInventoryView, markPOPaidView,
    editPOListView, editPOView, addCustomerView, addPatternView,
    shipBillView, generatePackingSlip, newPrintJobView,
    printJobsView, printJobNextStatusView, printJobPrevStatusView,
    printJobDetailView, printJobPDF,
    importAuditView,
)

urlpatterns = [
    path('', indexPageView, name='index'),
    path('newPO/', newPOView, name='newPO'),
    path('success/<int:invoice_id>/', successView, name='success'),
    path('invoice/<int:invoice_id>/pdf/', generatePDF, name='generatePDF'),

    # Dashboard actions
    path('expenses/new/', addExpenseView, name='addExpense'),
    path('inventory/', modifyInventoryView, name='modifyInventory'),
    path('po/mark-paid/', markPOPaidView, name='markPOPaid'),
    path('po/ship-bill/', shipBillView, name='shipBill'),
    path('po/<int:order_id>/packing-slip/', generatePackingSlip, name='packingSlip'),
    path('po/edit/', editPOListView, name='editPO'),
    path('po/edit/<int:order_id>/', editPOView, name='editPODetail'),
    path('customer/new/', addCustomerView, name='addCustomer'),
    path('pattern/new/', addPatternView, name='addPattern'),
    path('import-audit/', importAuditView, name='importAudit'),

    # Print jobs
    path('printjob/new/', newPrintJobView, name='newPrintJob'),
    path('printjobs/', printJobsView, name='printJobs'),
    path('printjob/<int:print_job_id>/', printJobDetailView, name='printJobDetail'),
    path('printjob/<int:print_job_id>/next/', printJobNextStatusView, name='printJobNext'),
    path('printjob/<int:print_job_id>/prev/', printJobPrevStatusView, name='printJobPrev'),
    path('printjob/<int:print_job_id>/pdf/', printJobPDF, name='printJobPDF'),
]