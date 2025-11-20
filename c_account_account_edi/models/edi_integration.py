from odoo import models, fields, api, _, Command
from odoo.exceptions import UserError
from datetime import datetime


class Integration(models.Model):
    _inherit = "edi.integration"

    type = fields.Selection(
        selection_add=[("load_moves_from_imos", "Load Moves from IMOS")],
        ondelete={"load_moves_from_imos": "cascade"},
    )

    # -------------------------------------------------------------------------
    # GENERIC HELPERS
    # -------------------------------------------------------------------------
    @staticmethod
    def _find_by_key(data, target_key):
        """Recursively extract all values for a given key inside nested dict/list."""
        results = []

        if isinstance(data, dict):
            for key, value in data.items():
                if key == target_key:
                    results.append(value)
                results.extend(Integration._find_by_key(value, target_key))

        elif isinstance(data, list):
            for item in data:
                results.extend(Integration._find_by_key(item, target_key))

        return results

    def _load_datas(self, all_items, item_key, model, field, company_ids=None):
        """
        Resolve IMOS codes into Odoo records.
        Supports multi-company search when needed.
        """
        company_ids = company_ids or []
        Model = self.env[model]

        # Extract raw values from XML
        items = list(set(Integration._find_by_key(all_items, item_key)))

        if not items:
            return Model.browse()

        # Build domain
        domain = [(field, 'in', items)]

        # Multi-company search if provided
        if company_ids:
            records = Model.browse()
            for company in company_ids:
                records |= Model.with_company(company).sudo().search(domain)
        else:
            records = Model.sudo().search(domain)

        # Check missing values
        missing_items = list(set(items) - set(records.mapped(field)))
        if missing_items:
            raise UserError(
                _("Missing %s records for values: %s") % (
                    model, ', '.join(missing_items)
                )
            )

        return records

    @staticmethod
    def _str_to_date(val):
        """Convert YYYY-MM-DD (or dirty input) into date safely."""
        try:
            val_str = str(val).strip()[:10]
            return datetime.strptime(val_str, '%Y-%m-%d').date()
        except Exception:
            return datetime.now().date()

    # -------------------------------------------------------------------------
    # MAIN PROCESSING
    # -------------------------------------------------------------------------
    def _process_content(self, data):
        self.ensure_one()
        if self.type == "load_moves_from_imos":
            return self._process_content_load_moves_from_imos(data)
        return super()._process_content(data)

    def _process_content_load_moves_from_imos(self, data):
        self.ensure_one()

        # -------------------------
        # XML → dict parsing
        # -------------------------
        try:
            all_items = self.env["xml.utils"].load_datas(data)
        except Exception as e:
            raise UserError(_("Invalid XML format: %s") % str(e))

        # -------------------------
        # Resolve all reference data
        # -------------------------
        all_companies = self._load_datas(all_items, "companyCode", "res.company", "name")
        all_partners = self._load_datas(all_items, "vendorShortName", "res.partner", "klnemo")
        all_currencies = self._load_datas(all_items, "currency", "res.currency", "name")
        # all_bill_codes = self._load_datas(all_items, "oprBillCode", "res.partner", "klnemo")
        all_accounts = self._load_datas(all_items, "ledgerCode", "account.account", "code", all_companies)
        all_taxes = self._load_datas(all_items, "taxCode", "account.tax", "name", all_companies)

        values = []

        # ---------------------------------------------------------------------
        # BUILD INVOICE PAYLOAD
        # ---------------------------------------------------------------------
        for items in all_items:
            invoice = items.get("invoice")
            if not invoice:
                continue

            # HEADER
            partner_code = invoice.get("vendorShortName")
            invoice_name = invoice.get("invoiceNo")
            chatter = invoice.get("memo")
            invoice_date = Integration._str_to_date(invoice.get("invoiceDate"))
            invoice_date_due = Integration._str_to_date(invoice.get("dueDate"))
            currency_code = invoice.get("currency")

            partner_id = all_partners.filtered(lambda p: p.klnemo == partner_code)
            currency_id = all_currencies.filtered(lambda c: c.name == currency_code)

            # DETAILS
            invoice_details = invoice.get("invoiceDetails", [])
            if isinstance(invoice_details, dict):
                invoice_details = [invoice_details]

            if not invoice_details:
                raise UserError(_("Missing <invoiceDetails> for invoice %s") % invoice_name)

            # Company comes from the first detail line
            company_code = invoice_details[0].get("companyCode")
            company_id = all_companies.filtered(lambda c: c.name == company_code)

            # -----------------------------------------------------------------
            # LINES
            # -----------------------------------------------------------------
            line_values = []

            for detail in invoice_details:
                opr_bill_code = detail.get("oprBillCode")
                account_code = detail.get("ledgerCode")
                tax_code = detail.get("taxCode")

                # Resolve references
                # bill_partner = all_bill_codes.filtered(lambda b: b.klnemo == opr_bill_code)
                account_id = all_accounts.filtered(lambda a: a.code == account_code)
                tax_ids = all_taxes.filtered(lambda t: t.name == tax_code)

                amount_currency = detail.get("currencyAmount")
                detail_name = detail.get("description") or "/"

                line_values.append(Command.create({
                    "account_id": account_id.id,
                    "name": detail_name,
                    "tax_ids": [Command.set(tax_ids.ids)],
                    "price_unit": amount_currency,
                    "quantity": 1,
                }))

            # -----------------------------------------------------------------
            # FINAL INVOICE VALUES
            # -----------------------------------------------------------------
            values.append({
                "company_id": company_id.id,
                "partner_id": partner_id.id,
                "name": invoice_name,
                "invoice_date": invoice_date,
                "invoice_date_due": invoice_date_due,
                "currency_id": currency_id.id,
                "invoice_line_ids": line_values,
                "move_type":"in_invoice",
                "message_ids": [
                    Command.create({
                        'model': 'account.move',
                        'body': chatter,
                    })
                ],
            })

        # ---------------------------------------------------------------------
        # CREATE INVOICES
        # ---------------------------------------------------------------------
        invoices = self.env["account.move"].sudo().create(values)
        return invoices
