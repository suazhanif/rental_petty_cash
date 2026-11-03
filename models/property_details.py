# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
from odoo import _, fields, models


class PropertyDetailsPettyCash(models.Model):
    _inherit = "property.details"

    petty_cash_ids = fields.One2many("petty.cash.transaction", "property_id")
    petty_cash_count = fields.Integer(compute="_compute_petty_cash")
    petty_cash_total = fields.Monetary(
        compute="_compute_petty_cash", string="Petty Cash Spent",
        currency_field="petty_cash_currency_id",
    )
    petty_cash_currency_id = fields.Many2one(
        "res.currency", compute="_compute_petty_cash"
    )

    def _compute_petty_cash(self):
        for rec in self:
            posted = rec.petty_cash_ids.filtered(
                lambda t: t.state == "posted" and t.transaction_type == "expense"
            )
            rec.petty_cash_count = len(posted)
            rec.petty_cash_total = sum(posted.mapped("amount_total"))
            rec.petty_cash_currency_id = (
                rec.company_id.currency_id if "company_id" in rec._fields and rec.company_id
                else self.env.company.currency_id
            )

    def action_view_petty_cash(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Petty Cash Expenses"),
            "res_model": "petty.cash.transaction",
            "view_mode": "list,form,pivot",
            "domain": [("property_id", "=", self.id), ("transaction_type", "=", "expense")],
            "context": {"default_property_id": self.id, "default_transaction_type": "expense"},
        }


class AccountMovePettyCash(models.Model):
    _inherit = "account.move"

    petty_cash_transaction_id = fields.Many2one(
        "petty.cash.transaction", string="Petty Cash Transaction",
        readonly=True, copy=False, index="btree_not_null",
    )
