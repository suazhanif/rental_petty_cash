# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
"""Petty cash top-ups and expenses.

Accounting created on Post:

* Top-up  : journal entry in the petty cash journal
            Dr Petty Cash account / Cr source (bank) journal account
* Expense : vendor bill with the expense lines and taxes (keeps vendor and
            VAT reporting correct), then a journal entry in the petty cash
            journal  Dr Vendor payable / Cr Petty Cash account, reconciled
            with the bill so the bill shows as Paid.

Cancel reverses the entries (no deletion), so it also works on locked journals.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PettyCashTransaction(models.Model):
    _name = "petty.cash.transaction"
    _description = "Petty Cash Transaction"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"

    name = fields.Char(default=lambda self: _("New"), readonly=True, copy=False)
    box_id = fields.Many2one(
        "petty.cash.box", string="Petty Cash", required=True, index=True,
        tracking=True, ondelete="restrict",
    )
    company_id = fields.Many2one(related="box_id.company_id", store=True)
    currency_id = fields.Many2one(related="box_id.currency_id")
    journal_id = fields.Many2one(related="box_id.journal_id", store=True)
    transaction_type = fields.Selection(
        [("expense", "Expense"), ("topup", "Add Balance")],
        string="Type", required=True, default="expense", tracking=True,
    )
    state = fields.Selection(
        [("draft", "Draft"), ("posted", "Posted"), ("cancel", "Cancelled")],
        default="draft", required=True, tracking=True, copy=False,
    )
    date = fields.Date(
        required=True, default=fields.Date.context_today, tracking=True
    )
    reference = fields.Char(
        string="Receipt / Ref No.", tracking=True,
        help="Receipt or invoice number for expenses; cheque/transfer number for top-ups.",
    )
    description = fields.Char(string="Purpose", tracking=True)
    user_id = fields.Many2one(
        "res.users", string="Recorded By", default=lambda self: self.env.user
    )

    # Expense
    partner_id = fields.Many2one("res.partner", string="Vendor", tracking=True)
    vendor_phone = fields.Char(related="partner_id.phone", string="Vendor Phone")
    vendor_vat = fields.Char(related="partner_id.vat", string="Vendor Tax ID")
    property_id = fields.Many2one(
        "property.details", string="Unit", tracking=True, index=True
    )
    tenancy_id = fields.Many2one(
        "tenancy.details", string="Contract",
        domain="[('property_id', '=', property_id)]",
    )
    project_id = fields.Many2one(
        related="property_id.property_project_id", string="Building", store=True
    )
    line_ids = fields.One2many(
        "petty.cash.transaction.line", "transaction_id", string="Expense Lines", copy=True
    )
    amount_untaxed = fields.Monetary(
        compute="_compute_amounts", store=True, string="Before Tax"
    )
    amount_tax = fields.Monetary(compute="_compute_amounts", store=True, string="Tax")
    amount_total = fields.Monetary(
        compute="_compute_amounts", store=True, string="Total"
    )

    # Top-up
    topup_amount = fields.Monetary(string="Amount to Add")
    source_journal_id = fields.Many2one(
        "account.journal",
        string="From (Bank / Cash)",
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id), "
        "('id', '!=', journal_id)]",
    )

    # Accounting links
    bill_id = fields.Many2one("account.move", string="Vendor Bill", readonly=True, copy=False)
    move_id = fields.Many2one(
        "account.move", string="Petty Cash Entry", readonly=True, copy=False
    )
    balance_after = fields.Monetary(
        string="Balance After", readonly=True, copy=False,
        help="Petty cash balance right after this transaction was posted.",
    )

    # ------------------------------------------------------------------
    # Computes / onchanges
    # ------------------------------------------------------------------
    @api.depends(
        "transaction_type", "topup_amount",
        "line_ids.price_subtotal", "line_ids.price_tax", "line_ids.price_total",
    )
    def _compute_amounts(self):
        for rec in self:
            if rec.transaction_type == "topup":
                rec.amount_untaxed = rec.topup_amount
                rec.amount_tax = 0.0
                rec.amount_total = rec.topup_amount
            else:
                rec.amount_untaxed = sum(rec.line_ids.mapped("price_subtotal"))
                rec.amount_tax = sum(rec.line_ids.mapped("price_tax"))
                rec.amount_total = sum(rec.line_ids.mapped("price_total"))

    @api.onchange("box_id", "transaction_type")
    def _onchange_box_defaults(self):
        if self.transaction_type == "expense" and self.box_id.default_vendor_id and not self.partner_id:
            self.partner_id = self.box_id.default_vendor_id

    @api.onchange("property_id")
    def _onchange_property_id(self):
        if self.tenancy_id and self.tenancy_id.property_id != self.property_id:
            self.tenancy_id = False
        if self.property_id and not self.tenancy_id:
            running = self.env["tenancy.details"].search(
                [("property_id", "=", self.property_id.id),
                 ("contract_type", "=", "running_contract")], limit=1
            )
            self.tenancy_id = running

    @api.onchange("tenancy_id")
    def _onchange_tenancy_id(self):
        if self.tenancy_id:
            self.property_id = self.tenancy_id.property_id

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "petty.cash.transaction"
                ) or _("New")
        return super().create(vals_list)

    def unlink(self):
        if any(rec.state == "posted" for rec in self):
            raise UserError(_("Posted petty cash transactions cannot be deleted. Cancel them first."))
        return super().unlink()

    @api.constrains("topup_amount", "transaction_type")
    def _check_topup(self):
        for rec in self:
            if rec.transaction_type == "topup" and rec.topup_amount < 0:
                raise ValidationError(_("The amount to add must be positive."))

    # ------------------------------------------------------------------
    # Posting
    # ------------------------------------------------------------------
    def action_post(self):
        for rec in self:
            if rec.state != "draft":
                continue
            if rec.transaction_type == "topup":
                rec._post_topup()
            else:
                rec._post_expense()
            rec.box_id.invalidate_recordset(["balance", "is_low_balance"])
            rec.write({"state": "posted", "balance_after": rec.box_id.balance})
        return True

    def _post_topup(self):
        self.ensure_one()
        if self.currency_id.compare_amounts(self.topup_amount, 0.0) <= 0:
            raise UserError(_("Enter the amount to add."))
        if not self.source_journal_id.default_account_id:
            raise UserError(_("Choose the bank or cash journal the money comes from."))
        label = _("Petty cash top-up %(name)s%(ref)s", name=self.name,
                  ref=f" - {self.reference}" if self.reference else "")
        move = self.env["account.move"].sudo().create({
            "move_type": "entry",
            "journal_id": self.journal_id.id,
            "date": self.date,
            "ref": label,
            "petty_cash_transaction_id": self.id,
            "line_ids": [
                fields.Command.create({
                    "name": label,
                    "account_id": self.box_id.cash_account_id.id,
                    "debit": self.topup_amount,
                    "credit": 0.0,
                }),
                fields.Command.create({
                    "name": label,
                    "account_id": self.source_journal_id.default_account_id.id,
                    "debit": 0.0,
                    "credit": self.topup_amount,
                }),
            ],
        })
        move.action_post()
        self.move_id = move

    def _post_expense(self):
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_("Select the vendor for this expense."))
        if not self.line_ids:
            raise UserError(_("Add at least one expense line."))
        for line in self.line_ids:
            if not line.account_id:
                raise UserError(_("Set an expense account on line '%s'.", line.name or ""))
            if line.price_unit <= 0 or line.quantity <= 0:
                raise UserError(_("Line '%s' needs a quantity and an amount.", line.name or ""))
        balance = self.box_id.balance
        if self.currency_id.compare_amounts(self.amount_total, balance) > 0:
            raise UserError(_(
                "Not enough petty cash: balance is %(bal)s, this expense is %(amt)s. "
                "Add balance first.",
                bal=self.currency_id.format(balance),
                amt=self.currency_id.format(self.amount_total),
            ))

        Move = self.env["account.move"].sudo()
        bill = Move.create({
            "move_type": "in_invoice",
            "partner_id": self.partner_id.id,
            "invoice_date": self.date,
            "date": self.date,
            "ref": self.reference or self.name,
            "narration": self._expense_narration(),
            "petty_cash_transaction_id": self.id,
            "invoice_line_ids": [
                fields.Command.create(line._prepare_bill_line_vals()) for line in self.line_ids
            ],
        })
        bill.action_post()

        payable = bill.line_ids.filtered(
            lambda l: l.account_id.account_type == "liability_payable"
        )
        label = _("Petty cash payment %(name)s - %(vendor)s",
                  name=self.name, vendor=self.partner_id.display_name)
        pay_lines = [
            fields.Command.create({
                "name": label,
                "account_id": pl.account_id.id,
                "partner_id": pl.partner_id.id,
                "debit": -pl.balance if pl.balance < 0 else 0.0,
                "credit": pl.balance if pl.balance > 0 else 0.0,
                "date_maturity": self.date,
            })
            for pl in payable
        ]
        pay_lines.append(fields.Command.create({
            "name": label,
            "account_id": self.box_id.cash_account_id.id,
            "partner_id": self.partner_id.id,
            "debit": 0.0,
            "credit": -sum(payable.mapped("balance")),
        }))
        payment = Move.create({
            "move_type": "entry",
            "journal_id": self.journal_id.id,
            "date": self.date,
            "ref": label,
            "petty_cash_transaction_id": self.id,
            "line_ids": pay_lines,
        })
        payment.action_post()
        (payable | payment.line_ids.filtered(
            lambda l: l.account_id.account_type == "liability_payable"
        )).reconcile()
        self.write({"bill_id": bill.id, "move_id": payment.id})

    def _expense_narration(self):
        parts = []
        if self.description:
            parts.append(self.description)
        if self.property_id:
            parts.append(_("Unit: %s", self.property_id.display_name))
        if self.tenancy_id:
            parts.append(_("Contract: %s", self.tenancy_id.tenancy_seq))
        parts.append(_("Paid from petty cash %s", self.box_id.name))
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Cancel / reset
    # ------------------------------------------------------------------
    def action_cancel(self):
        for rec in self:
            if rec.state == "posted":
                moves = (rec.bill_id | rec.move_id).filtered(lambda m: m.state == "posted")
                moves.line_ids.remove_move_reconcile()
                if moves:
                    moves._reverse_moves(
                        default_values_list=[
                            {"date": fields.Date.context_today(rec),
                             "ref": _("Cancel %(name)s: %(ref)s", name=rec.name, ref=m.ref or m.name)}
                            for m in moves
                        ],
                        cancel=True,
                    )
            rec.state = "cancel"
        return True

    def action_reset_draft(self):
        for rec in self:
            if rec.state != "cancel":
                continue
            rec.write({"state": "draft", "bill_id": False, "move_id": False, "balance_after": 0.0})
        return True

    def _open_move(self, move):
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": move.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_bill(self):
        self.ensure_one()
        return self._open_move(self.bill_id)

    def action_open_entry(self):
        self.ensure_one()
        return self._open_move(self.move_id)

    def action_print_voucher(self):
        return self.env.ref("rental_petty_cash.action_report_petty_cash_voucher").report_action(self)

    def action_save_close(self):
        """Save button of the quick-entry dialog"""
        return {"type": "ir.actions.act_window_close"}

    def action_save_post(self):
        """Save & Post button of the quick-entry dialog"""
        self.action_post()
        return {"type": "ir.actions.act_window_close"}


class PettyCashTransactionLine(models.Model):
    _name = "petty.cash.transaction.line"
    _description = "Petty Cash Expense Line"

    transaction_id = fields.Many2one(
        "petty.cash.transaction", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="transaction_id.company_id")
    currency_id = fields.Many2one(related="transaction_id.currency_id")
    product_id = fields.Many2one(
        "product.product", string="Item",
        domain="[('purchase_ok', '=', True)]",
    )
    name = fields.Char(string="Description", required=True)
    account_id = fields.Many2one(
        "account.account", string="Expense Account",
        domain="[('account_type', 'in', ('expense', 'expense_direct_cost', 'asset_current'))]",
    )
    quantity = fields.Float(default=1.0, digits="Product Unit")
    price_unit = fields.Monetary(string="Unit Price (Before Tax)")
    tax_ids = fields.Many2many(
        "account.tax", string="Taxes",
        domain="[('type_tax_use', '=', 'purchase'), ('company_id', 'parent_of', company_id)]",
    )
    price_subtotal = fields.Monetary(compute="_compute_amounts", store=True, string="Before Tax")
    price_tax = fields.Monetary(compute="_compute_amounts", store=True, string="Tax")
    price_total = fields.Monetary(compute="_compute_amounts", store=True, string="Total")

    @api.depends("quantity", "price_unit", "tax_ids", "currency_id")
    def _compute_amounts(self):
        for line in self:
            if line.tax_ids:
                res = line.tax_ids.compute_all(
                    line.price_unit, currency=line.currency_id, quantity=line.quantity,
                    product=line.product_id, partner=line.transaction_id.partner_id,
                )
                line.price_subtotal = res["total_excluded"]
                line.price_total = res["total_included"]
            else:
                line.price_subtotal = line.price_total = line.quantity * line.price_unit
            line.price_tax = line.price_total - line.price_subtotal

    @api.onchange("product_id")
    def _onchange_product_id(self):
        product = self.product_id
        if not product:
            return
        self.name = product.display_name
        if product.standard_price and not self.price_unit:
            self.price_unit = product.standard_price
        accounts = product.product_tmpl_id.with_company(self.company_id)._get_product_accounts()
        if accounts.get("expense"):
            self.account_id = accounts["expense"]
        company = self.company_id or self.env.company
        self.tax_ids = product.supplier_taxes_id.filtered(
            lambda t: t.company_id in company.parent_ids
        )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines.filtered(lambda l: not l.account_id):
            default = line.transaction_id.box_id.default_expense_account_id
            if default:
                line.account_id = default
        return lines

    def _prepare_bill_line_vals(self):
        self.ensure_one()
        txn = self.transaction_id
        name = self.name
        if txn.property_id:
            name = f"{name} [{txn.property_id.display_name}]"
        vals = {
            "name": name,
            "product_id": self.product_id.id,
            "account_id": self.account_id.id,
            "quantity": self.quantity,
            "price_unit": self.price_unit,
            "tax_ids": [fields.Command.set(self.tax_ids.ids)],
        }
        return vals
