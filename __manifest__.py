# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
{
    "name": "Rental Management - Petty Cash",
    "summary": "Petty cash and bank statement lines with Unit / Contract / Purpose, per-unit reporting",
    "description": """
Petty Cash for Rental Management
================================
* Petty Cash boxes, each linked to a Cash journal, with live balance
* Add Balance (top-up from a bank/cash journal) from the same screen
* Record expenses with vendor, receipt no., unit / contract, lines with
  untaxed amount, taxes and total; posting creates the vendor bill and
  pays it from petty cash automatically
* Receipts attached in the chatter, printable Petty Cash Voucher
* Petty cash expenses visible on each unit
* Bank statement lines with Unit, Contract, Purpose and Category:
  Excel import template, auto-detection of the contract from the bank
  label, editable Bank Lines list with Reconcile button, Bank by Unit
  report and Bank smart buttons on contracts and units
    """,
    "author": "Baramej® Business and Digital Technology Center LLC",
    "website": "https://baramej.io/en",
    "category": "Realestate",
    "version": "19.0.1.0.0",
    "license": "OPL-1",
    "depends": ["rental_management", "account"],
    "data": [
        "security/ir.model.access.csv",
        "security/petty_cash_security.xml",
        "data/petty_cash_sequence.xml",
        "views/petty_cash_transaction_views.xml",
        "views/petty_cash_box_views.xml",
        "views/property_details_views.xml",
        "views/bank_statement_line_views.xml",
        "views/menus.xml",
        "report/petty_cash_voucher.xml",
    ],
    "installable": True,
    "application": False,
}
