# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
"""Rental details on bank statement lines.

Adds Unit / Contract / Purpose / Category to every bank (and cash) statement
line so they can be imported from Excel together with Date, Label, Partner,
Reference and Amount, edited in the "Bank Lines" list, and reported per unit.

* Choosing (or importing) a Contract fills the Unit and, when empty, the
  Partner (tenant). With the partner set, the standard Enterprise bank
  reconciliation proposes that tenant's open invoices first.
* When nothing is set, the contract / unit number is detected in the bank
  label or reference (e.g. "TRF RENT T/00123").
* The contract is also written on the statement's journal entry
  (account.move.tenancy_id), so the bank entry itself points to the contract.
"""
import re

from odoo import _, api, fields, models

TOKEN_RE = re.compile(r"[A-Za-z]{1,10}[/\-][A-Za-z0-9/\-]*\d")


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    bank_tenancy_id = fields.Many2one(
        "tenancy.details", string="Contract", index="btree_not_null",
        help="Rent contract this bank transaction is for.",
    )
    bank_property_id = fields.Many2one(
        "property.details", string="Unit", index="btree_not_null",
    )
    bank_project_id = fields.Many2one(
        related="bank_property_id.property_project_id", string="Building", store=True
    )
    bank_purpose = fields.Char(string="Purpose")
    bank_category = fields.Selection(
        [
            ("rent", "Rent"),
            ("deposit", "Security Deposit"),
            ("maintenance", "Maintenance / Repair"),
            ("utility", "Utility"),
            ("pdc", "PDC Cheque"),
            ("petty_cash", "Petty Cash Transfer"),
            ("vendor", "Vendor Payment"),
            ("bank_charges", "Bank Charges"),
            ("other", "Other"),
        ],
        string="Category",
    )
    amount_in = fields.Monetary(
        compute="_compute_in_out", store=True, string="Money In",
        currency_field="currency_id",
    )
    amount_out = fields.Monetary(
        compute="_compute_in_out", store=True, string="Money Out",
        currency_field="currency_id",
    )

    @api.depends("amount")
    def _compute_in_out(self):
        for line in self:
            line.amount_in = line.amount if line.amount > 0 else 0.0
            line.amount_out = -line.amount if line.amount < 0 else 0.0

    # ------------------------------------------------------------------
    # Auto fill
    # ------------------------------------------------------------------
    @api.onchange("bank_tenancy_id")
    def _onchange_bank_tenancy_id(self):
        tenancy = self.bank_tenancy_id
        if tenancy:
            self.bank_property_id = tenancy.property_id
            if not self.partner_id and tenancy.tenancy_id:
                self.partner_id = tenancy.tenancy_id
            if not self.bank_category and self.amount > 0:
                self.bank_category = "rent"

    @api.onchange("bank_property_id")
    def _onchange_bank_property_id(self):
        if self.bank_tenancy_id and self.bank_tenancy_id.property_id != self.bank_property_id:
            self.bank_tenancy_id = False

    def _rental_tokens(self):
        """Candidate contract / unit numbers found in the label and reference"""
        self.ensure_one()
        text = " ".join(filter(None, [self.payment_ref, self.ref, self.bank_purpose]))
        tokens = {t.upper().strip("-/") for t in TOKEN_RE.findall(text or "")}
        return list(tokens)

    def _rental_autofill(self):
        """Fill unit / partner from the contract, or detect them from the label"""
        Tenancy = self.env["tenancy.details"].sudo()
        Property = self.env["property.details"].sudo()
        for line in self:
            vals = {}
            tenancy = line.bank_tenancy_id
            prop = line.bank_property_id
            if not tenancy and not prop:
                tokens = line._rental_tokens()
                if tokens:
                    tenancy = Tenancy.search([("tenancy_seq", "in", tokens)], limit=1)
                    if tenancy:
                        vals["bank_tenancy_id"] = tenancy.id
                    else:
                        prop = Property.search([("property_seq", "in", tokens)], limit=1)
                        if prop:
                            vals["bank_property_id"] = prop.id
            if tenancy:
                if line.bank_property_id != tenancy.property_id:
                    vals["bank_property_id"] = tenancy.property_id.id
                if not line.partner_id and tenancy.tenancy_id and not line.is_reconciled:
                    vals["partner_id"] = tenancy.tenancy_id.id
                if not line.bank_category and line.amount > 0:
                    vals["bank_category"] = "rent"
            if vals:
                super(AccountBankStatementLine, line).write(vals)
            line._rental_sync_move()

    def _rental_sync_move(self):
        """Put the contract on the bank journal entry as well"""
        for line in self:
            move = line.move_id
            if move and "tenancy_id" in move._fields and move.tenancy_id != line.bank_tenancy_id:
                move.sudo().write({"tenancy_id": line.bank_tenancy_id.id})

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._rental_autofill()
        return lines

    def write(self, vals):
        res = super().write(vals)
        if {"bank_tenancy_id", "bank_property_id", "payment_ref", "ref"} & set(vals):
            self._rental_autofill()
        return res

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------
    def action_detect_rental(self):
        """Re-run detection on selected lines (list action)"""
        self._rental_autofill()
        return True

    def action_open_reconcile(self):
        """Open the Enterprise bank reconciliation on this line's journal"""
        self.ensure_one()
        opener = getattr(self, "_action_open_bank_reconciliation_widget", None)
        if opener:
            return opener(
                extra_domain=[("journal_id", "=", self.journal_id.id)],
                default_context={
                    "default_journal_id": self.journal_id.id,
                    "default_st_line_id": self.id,
                },
            )
        if hasattr(self.journal_id, "action_open_reconcile"):
            return self.journal_id.action_open_reconcile()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.move_id.id,
            "view_mode": "form",
        }


class AccountJournalBank(models.Model):
    _inherit = "account.journal"

    def action_open_rental_bank_lines(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rental_petty_cash.action_rental_bank_lines"
        )
        action["domain"] = [("journal_id", "=", self.id)]
        action["context"] = {"default_journal_id": self.id, "search_default_not_reconciled": 1}
        return action


class TenancyDetailsBank(models.Model):
    _inherit = "tenancy.details"

    bank_line_count = fields.Integer(compute="_compute_bank_lines")

    def _compute_bank_lines(self):
        Line = self.env["account.bank.statement.line"].sudo()
        for rec in self:
            rec.bank_line_count = Line.search_count([("bank_tenancy_id", "=", rec.id)]) if rec.id else 0

    def action_view_bank_lines(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rental_petty_cash.action_rental_bank_lines"
        )
        action["domain"] = [("bank_tenancy_id", "=", self.id)]
        action["context"] = {"default_bank_tenancy_id": self.id}
        action["name"] = _("Bank Transactions")
        return action


class PropertyDetailsBank(models.Model):
    _inherit = "property.details"

    bank_line_count = fields.Integer(compute="_compute_bank_lines")

    def _compute_bank_lines(self):
        Line = self.env["account.bank.statement.line"].sudo()
        for rec in self:
            rec.bank_line_count = Line.search_count([("bank_property_id", "=", rec.id)]) if rec.id else 0

    def action_view_bank_lines(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rental_petty_cash.action_rental_bank_lines"
        )
        action["domain"] = [("bank_property_id", "=", self.id)]
        action["context"] = {"default_bank_property_id": self.id}
        action["name"] = _("Bank Transactions")
        return action
