# -*- coding: utf-8 -*-
# Copyright 2026-Baramej®.
# Part of Baramej®. See LICENSE file for full copyright and licensing details.
"""Import a bank statement from the rental template (Excel or CSV).

The wizard reads the file itself, so there is no column-mapping screen:
pick the bank, choose the file, click Import. Columns are found by their
title (case and spaces ignored), with common bank synonyms accepted.
Nothing is created if any row has an error; all errors are listed at once.
"""
import base64
import csv
import io
import re
from datetime import date, datetime

from odoo import _, fields, models
from odoo.exceptions import UserError

# column title (normalised) -> internal key
COLUMN_ALIASES = {
    "date": "date", "transactiondate": "date", "valuedate": "date", "postingdate": "date",
    "label": "label", "description": "label", "narration": "label", "details": "label",
    "transactiondetails": "label", "particulars": "label",
    "partner": "partner", "customervendor": "partner", "name": "partner",
    "reference": "reference", "ref": "reference", "chequeno": "reference",
    "chequenumber": "reference", "refno": "reference",
    "amount": "amount",
    "moneyin": "credit", "credit": "credit", "deposit": "credit", "deposits": "credit",
    "moneyout": "debit", "debit": "debit", "withdrawal": "debit", "withdrawals": "debit",
    "contract": "contract", "contractno": "contract", "contractnumber": "contract",
    "unit": "unit", "property": "unit",
    "purpose": "purpose", "notes": "purpose", "remarks": "purpose",
    "category": "category",
}
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y",
                "%d %b %Y", "%d-%b-%Y", "%d/%m/%y", "%Y/%m/%d")


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


class RentalBankImportWizard(models.TransientModel):
    _name = "rental.bank.import.wizard"
    _description = "Import Bank Statement (with Unit / Contract)"

    journal_id = fields.Many2one(
        "account.journal", string="Bank / Cash Journal", required=True,
        domain="[('type', 'in', ('bank', 'cash'))]",
        default=lambda self: self.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", self.env.company.id)], limit=1
        ),
    )
    file = fields.Binary(string="Statement File (.xlsx / .csv)")
    filename = fields.Char()
    skip_duplicates = fields.Boolean(
        string="Skip lines already imported", default=True,
        help="Skip rows with the same date, amount, label and reference that "
        "already exist in this journal.",
    )

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------
    def action_download_template(self):
        return {
            "type": "ir.actions.act_url",
            "url": "/rental_petty_cash/static/xlsx/bank_statement_import_template.xlsx",
            "target": "download",
        }

    def action_import(self):
        self.ensure_one()
        if not self.file:
            raise UserError(_("Choose the Excel or CSV file to import."))
        rows = self._read_rows()
        vals_list, errors, skipped = self._prepare_lines(rows)
        if errors:
            raise UserError(
                _("Nothing was imported. Please fix these rows and try again:\n\n%s")
                % "\n".join(errors[:50])
            )
        if not vals_list:
            raise UserError(_("No new lines found in the file."))
        lines = self.env["account.bank.statement.line"].create(vals_list)
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "rental_petty_cash.action_rental_bank_lines"
        )
        action["domain"] = [("id", "in", lines.ids)]
        action["context"] = {}
        action["name"] = (
            _("Imported Bank Lines (%(n)s new, %(s)s duplicates skipped)", n=len(lines), s=skipped)
            if skipped else _("Imported Bank Lines (%s)", len(lines))
        )
        return action

    # ------------------------------------------------------------------
    # Reading the file
    # ------------------------------------------------------------------
    def _read_rows(self):
        """Return a list of dicts {key: raw value, '_row': row number}"""
        content = base64.b64decode(self.file)
        name = (self.filename or "").lower()
        if name.endswith(".csv") or name.endswith(".txt"):
            table = self._read_csv(content)
        else:
            table = self._read_xlsx(content)
        header_idx, columns = self._find_header(table)
        rows = []
        for idx, raw in enumerate(table[header_idx + 1:], start=header_idx + 2):
            if not any(str(v).strip() for v in raw if v is not None):
                continue
            row = {"_row": idx}
            for col, key in columns.items():
                row[key] = raw[col] if col < len(raw) else None
            rows.append(row)
        return rows

    def _read_xlsx(self, content):
        try:
            import openpyxl
        except ImportError as exc:
            raise UserError(_("The Python library openpyxl is needed to read Excel files.")) from exc
        try:
            wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
        except Exception as exc:
            raise UserError(_("Could not read the Excel file. Save it as .xlsx (or .csv) and try again.")) from exc
        sheet = wb.worksheets[0]
        return [list(r) for r in sheet.iter_rows(values_only=True)]

    def _read_csv(self, content):
        for encoding in ("utf-8-sig", "cp1256", "latin-1"):
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t") if text else csv.excel
        return [row for row in csv.reader(io.StringIO(text), dialect)]

    def _find_header(self, table):
        """First row (within the top 15) that has a Date column and Amount or In/Out"""
        for idx, raw in enumerate(table[:15]):
            columns = {}
            for col, title in enumerate(raw):
                key = COLUMN_ALIASES.get(_norm(title))
                if key and key not in columns.values():
                    columns[col] = key
            keys = set(columns.values())
            if "date" in keys and ("amount" in keys or {"credit", "debit"} & keys):
                return idx, columns
        raise UserError(_(
            "Could not find the column titles. The file needs at least a 'Date' "
            "column and an 'Amount' column (or 'Money In' / 'Money Out'). "
            "Use Download Template to get the expected layout."
        ))

    # ------------------------------------------------------------------
    # Converting rows
    # ------------------------------------------------------------------
    def _parse_date(self, value):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, (int, float)) and value > 20000:  # Excel serial
            return date.fromordinal(date(1899, 12, 30).toordinal() + int(value))
        text = str(value or "").strip()
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

    def _parse_amount(self, value):
        if value in (None, ""):
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace(",", "").replace(" ", "")
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()")
        if text.upper().endswith("DR"):
            negative, text = True, text[:-2]
        elif text.upper().endswith("CR"):
            text = text[:-2]
        try:
            amount = float(text)
        except ValueError:
            return None
        return -amount if negative else amount

    def _category_map(self):
        field = self.env["account.bank.statement.line"]._fields["bank_category"]
        mapping = {}
        for key, label in field._description_selection(self.env):
            mapping[_norm(key)] = key
            mapping[_norm(label)] = key
        return mapping

    def _prepare_lines(self, rows):
        Partner = self.env["res.partner"]
        Tenancy = self.env["tenancy.details"]
        Property = self.env["property.details"]
        Line = self.env["account.bank.statement.line"]
        categories = self._category_map()
        cache = {}

        def find(model, field, value, extra=None):
            key = (model._name, field, value)
            if key not in cache:
                domain = [(field, "=ilike", value)] + (extra or [])
                cache[key] = model.search(domain, limit=2)
            return cache[key]

        vals_list, errors, skipped = [], [], 0
        for row in rows:
            n = row["_row"]
            problems = []
            line_date = self._parse_date(row.get("date"))
            if not line_date:
                problems.append(_("date '%s' not recognised", row.get("date")))
            if "amount" in row:
                amount = self._parse_amount(row.get("amount"))
            else:
                credit = self._parse_amount(row.get("credit"))
                debit = self._parse_amount(row.get("debit"))
                amount = None if credit is None or debit is None else credit - abs(debit)
            if amount is None:
                problems.append(_("amount not a number"))
            elif not amount:
                problems.append(_("amount is zero"))
            label = str(row.get("label") or "").strip()
            reference = str(row.get("reference") or "").strip()
            if reference.endswith(".0"):
                reference = reference[:-2]

            vals = {
                "journal_id": self.journal_id.id,
                "date": line_date,
                "amount": amount or 0.0,
                "payment_ref": label or reference or "/",
                "ref": reference or False,
                "bank_purpose": str(row.get("purpose") or "").strip() or False,
            }

            partner_name = str(row.get("partner") or "").strip()
            if partner_name:
                partner = find(Partner, "name", partner_name)
                if len(partner) == 1:
                    vals["partner_id"] = partner.id
                else:
                    # unknown or ambiguous name: keep it, the bank screen can match it
                    vals["partner_name"] = partner_name

            contract_no = str(row.get("contract") or "").strip()
            if contract_no:
                tenancy = find(Tenancy, "tenancy_seq", contract_no)
                if len(tenancy) == 1:
                    vals["bank_tenancy_id"] = tenancy.id
                else:
                    problems.append(_("contract '%s' not found", contract_no))

            unit = str(row.get("unit") or "").strip()
            if unit:
                prop = find(Property, "name", unit) or find(Property, "property_seq", unit)
                if len(prop) == 1:
                    vals["bank_property_id"] = prop.id
                elif len(prop) > 1:
                    problems.append(_("unit '%s' matches several units, use the unit code", unit))
                else:
                    problems.append(_("unit '%s' not found", unit))

            category = str(row.get("category") or "").strip()
            if category:
                key = categories.get(_norm(category))
                if key:
                    vals["bank_category"] = key
                else:
                    problems.append(_("category '%s' not in the list", category))

            if problems:
                errors.append(_("Row %(n)s: %(p)s", n=n, p="; ".join(problems)))
                continue

            if self.skip_duplicates and Line.search_count([
                ("journal_id", "=", self.journal_id.id),
                ("date", "=", line_date),
                ("amount", "=", amount),
                ("payment_ref", "=", vals["payment_ref"]),
                ("ref", "=", vals["ref"]),
            ]):
                skipped += 1
                continue
            vals_list.append(vals)
        return vals_list, errors, skipped