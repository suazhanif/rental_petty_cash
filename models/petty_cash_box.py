# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PettyCashBox(models.Model):
    """A petty cash fund, backed by one Cash journal.

    The balance is read from accounting (posted entries on the journal's
    cash account), so it always matches the General Ledger.
    """

    _name = "petty.cash.box"
    _description = "Petty Cash Box"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    journal_id = fields.Many2one(
        "account.journal",
        string="Petty Cash Journal",
        required=True,
        tracking=True,
        domain="[('type', '=', 'cash'), ('company_id', '=', company_id)]",
    )
    cash_account_id = fields.Many2one(
        related="journal_id.default_account_id", string="Cash Account"
    )
    currency_id = fields.Many2one(
        "res.currency",
        compute="_compute_currency_id",
    )
    custodian_id = fields.Many2one(
        "res.users", string="Custodian", tracking=True,
        help="Person holding and responsible for this cash.",
    )
    float_amount = fields.Monetary(
        string="Float / Limit",
        tracking=True,
        help="Agreed petty cash float. The box shows a warning when the balance "
        "falls below the Low Balance Alert.",
    )
    low_balance_alert = fields.Monetary(
        string="Low Balance Alert", tracking=True,
        help="Show a warning when the balance drops below this amount.",
    )
    default_expense_account_id = fields.Many2one(
        "account.account",
        string="Default Expense Account",
        domain="[('account_type', 'in', ('expense', 'expense_direct_cost'))]",
        help="Used on expense lines when no product/account is chosen.",
    )
    default_vendor_id = fields.Many2one(
        "res.partner",
        string="Default Vendor",
        help="Generic vendor for small receipts (e.g. 'Petty Cash Vendor').",
    )

    transfer_account_id = fields.Many2one(
        "account.account",
        string="Liquidity Transfer Account",
        tracking=True,
        default=lambda self: self.env.company.transfer_account_id,
        domain="[('reconcile', '=', True), ('account_type', 'in', ('asset_current', 'liability_current'))]",
        help="Intermediate account used for top-ups: the bank leg and the petty cash "
        "leg both go through this account and are reconciled together, so each side "
        "shows in its journal's Balance (cash register) instead of Misc. "
        "Must allow reconciliation.",
    )

    transaction_ids = fields.One2many("petty.cash.transaction", "box_id")
    balance = fields.Monetary(compute="_compute_balance", string="Current Balance")
    is_low_balance = fields.Boolean(compute="_compute_balance")
    total_topup = fields.Monetary(compute="_compute_totals", string="Total Added")
    total_expense = fields.Monetary(compute="_compute_totals", string="Total Spent")
    draft_count = fields.Integer(compute="_compute_totals", string="To Post")
    draft_amount = fields.Monetary(compute="_compute_totals", string="Pending Amount")
    expense_count = fields.Integer(compute="_compute_totals")
    topup_count = fields.Integer(compute="_compute_totals")

    _journal_unique = models.Constraint(
        "UNIQUE(journal_id)", "This journal is already used by another petty cash box."
    )

    @api.depends("journal_id", "company_id")
    def _compute_currency_id(self):
        for box in self:
            box.currency_id = box.journal_id.currency_id or box.company_id.currency_id

    @api.depends("journal_id", "low_balance_alert")
    def _compute_balance(self):
        MoveLine = self.env["account.move.line"].sudo()
        for box in self:
            account = box.journal_id.default_account_id
            if not account:
                box.balance = 0.0
                box.is_low_balance = False
                continue
            domain = [
                ("account_id", "=", account.id),
                ("parent_state", "=", "posted"),
                ("company_id", "=", box.company_id.id),
            ]
            field = "amount_currency" if box.journal_id.currency_id else "balance"
            groups = MoveLine._read_group(domain, aggregates=[f"{field}:sum"])
            box.balance = (groups[0][0] if groups else 0.0) or 0.0
            box.is_low_balance = bool(
                box.low_balance_alert and box.balance < box.low_balance_alert
            )

    @api.depends("transaction_ids.state", "transaction_ids.amount_total")
    def _compute_totals(self):
        for box in self:
            posted = box.transaction_ids.filtered(lambda t: t.state == "posted")
            draft = box.transaction_ids.filtered(lambda t: t.state == "draft")
            topups = posted.filtered(lambda t: t.transaction_type == "topup")
            expenses = posted.filtered(lambda t: t.transaction_type == "expense")
            box.total_topup = sum(topups.mapped("amount_total"))
            box.total_expense = sum(expenses.mapped("amount_total"))
            box.draft_count = len(draft)
            box.draft_amount = sum(
                draft.filtered(lambda t: t.transaction_type == "expense").mapped("amount_total")
            )
            box.expense_count = len(box.transaction_ids.filtered(lambda t: t.transaction_type == "expense"))
            box.topup_count = len(box.transaction_ids.filtered(lambda t: t.transaction_type == "topup"))

    @api.constrains("journal_id", "company_id")
    def _check_journal(self):
        for box in self:
            if box.journal_id.type != "cash":
                raise ValidationError(_("The petty cash journal must be a Cash journal."))
            if not box.journal_id.default_account_id:
                raise ValidationError(_("The journal %s has no cash account set.", box.journal_id.name))

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------
    def _open_new_transaction(self, transaction_type):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Add Balance") if transaction_type == "topup" else _("New Expense"),
            "res_model": "petty.cash.transaction",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_box_id": self.id,
                "default_transaction_type": transaction_type,
            },
        }

    def action_add_balance(self):
        return self._open_new_transaction("topup")

    def action_new_expense(self):
        return self._open_new_transaction("expense")

    def _open_transactions(self, domain, name, transaction_type=None):
        self.ensure_one()
        ctx = {"default_box_id": self.id, "search_default_group_by_month": 0}
        if transaction_type:
            ctx["default_transaction_type"] = transaction_type
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": "petty.cash.transaction",
            "view_mode": "list,form,pivot",
            "domain": [("box_id", "=", self.id)] + domain,
            "context": ctx,
        }

    def action_view_expenses(self):
        return self._open_transactions(
            [("transaction_type", "=", "expense")], _("Expenses"), "expense"
        )

    def action_view_topups(self):
        return self._open_transactions(
            [("transaction_type", "=", "topup")], _("Top-ups"), "topup"
        )

    def action_view_pending(self):
        return self._open_transactions([("state", "=", "draft")], _("To Post"))

    def action_view_ledger(self):
        """Every accounting movement on the petty cash account"""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Petty Cash Ledger"),
            "res_model": "account.move.line",
            "view_mode": "list",
            "domain": [
                ("account_id", "=", self.journal_id.default_account_id.id),
                ("parent_state", "=", "posted"),
            ],
            "context": {"search_default_group_by_move": 0},
        }
